"""Добровольно включаемый обмен с Android в доверенной локальной сети.

Квитанция и изменение записываются одной транзакцией. Изменение поля
принимается только при совпадении исходного значения (трёхстороннее слияние).
HTTP не шифруется: этот интерфейс нельзя публиковать в интернете.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import uuid
from datetime import timedelta, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import ValidationError, clean_task, parse_day, scheduled, as_bool

FIELDS = ("title", "notes", "category", "priority", "recurrence",
          "start_date", "time", "reminder", "reminder_end_date", "event_date", "escalation_days")
SNAPSHOT = ("title", "notes", "category", "priority", "time", "reminder")
LOGGER = logging.getLogger(__name__)


def identifier(value):
    """Принимать только канонический UUID, общий для обеих платформ."""
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError("Некорректный идентификатор изменения.") from error
    return value


def normalized(row):
    """Привести SQLite boolean к типу JSON, который использует телефон."""
    result = dict(row)
    if "reminder" in result:
        result["reminder"] = bool(result["reminder"])
    return result


class SyncEngine:
    """Применять независимые команды, оставляя конфликты для выбора человека."""

    def __init__(self, store, server_id):
        self.store, self.server_id = store, server_id
        with store.connection(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS mobile_receipts "
                       "(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")

    def exchange(self, body):
        """Вернуть квитанции и согласованный снимок в той же транзакции."""
        if body.get("protocol") != 2:
            raise ValidationError("Обнови обе версии Ритма: протокол отличается.", 409)
        if body.get("server_id") not in (None, "", self.server_id):
            raise ValidationError("Это другая база ноутбука. Подключение отменено.", 409)
        operations = body.get("operations", [])
        if not isinstance(operations, list) or len(operations) > 100:
            raise ValidationError("Отправляй не больше 100 изменений за один запрос.")
        self.store.refresh()
        accepted, conflicts = [], []
        with self.store.connection(write=True) as db:
            for op in operations:
                if not isinstance(op, dict):
                    raise ValidationError("Изменение должно быть объектом.")
                op_id = identifier(op.get("id"))
                fingerprint = hashlib.sha256(json.dumps(
                    op, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                previous = db.execute("SELECT fingerprint FROM mobile_receipts WHERE id=?",
                                      (op_id,)).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        raise ValidationError("Идентификатор повторён с другими данными.", 409)
                    accepted.append(op_id)
                    continue
                db.execute("SAVEPOINT operation")
                try:
                    self._apply(db, op)
                    db.execute("INSERT INTO mobile_receipts VALUES (?,?)", (op_id, fingerprint))
                    accepted.append(op_id)
                except ValidationError as error:
                    db.execute("ROLLBACK TO operation")
                    conflicts.append({"id": op_id, "reason": str(error)})
                finally:
                    db.execute("RELEASE operation")
            tasks = [normalized(row) for row in db.execute("SELECT * FROM tasks")]
            occurrences = [normalized(row) for row in db.execute(
                "SELECT task_id,date,title,notes,category,priority,time,reminder,"
                "status,completed_at,dismissed_at FROM occurrences WHERE status!='cancelled'")]
        return {"protocol": 2, "server_id": self.server_id, "accepted": accepted,
                "conflicts": conflicts, "tasks": tasks, "occurrences": occurrences,
                "server_time": self.store.clock().isoformat()}

    def _apply(self, db, op):
        """Выполнить одну валидированную операцию внутри savepoint."""
        task_id = identifier(op.get("task_id"))
        kind = op.get("kind")
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        current = normalized(row) if row else None
        today = self.store.clock().date()
        if kind == "create":
            if current:
                raise ValidationError("Задача с этим идентификатором уже существует.", 409)
            values = op.get("values")
            if not isinstance(values, dict):
                raise ValidationError("Нет данных задачи.")
            start = parse_day(values.get("start_date"))
            if start < today - timedelta(days=3650):
                raise ValidationError("Задача слишком старая для импорта.")
            task = clean_task(values, min(today, start))
            task.update(id=task_id, created_at=self.store.clock().isoformat(), archived_date=None)
            db.execute("INSERT INTO tasks (" + ",".join(task) + ") VALUES ("
                       + ",".join("?" for _ in task) + ")", tuple(task.values()))
            # Сохранить каждый офлайн-день до применения следующей правки шаблона.
            day = start
            while day <= today:
                if scheduled(task, day):
                    db.execute("INSERT OR IGNORE INTO occurrences "
                               "(task_id,date,title,notes,category,priority,time,reminder) VALUES (?,?,?,?,?,?,?,?)",
                               (task_id, day.isoformat(), task["title"], task["notes"], task["category"],
                                task["priority"], task["time"], task["reminder"]))
                if task["recurrence"] == "once":
                    break
                day += timedelta(days=1)
            self.store._materialize(db, start)
            self.store._materialize(db, today)
            return
        if not current:
            raise ValidationError("На ноутбуке нет этой задачи.", 409)
        if kind == "dismiss":
            day = parse_day(op.get("date"))
            row = db.execute("SELECT * FROM occurrences WHERE task_id=? AND date=?", (task_id, day.isoformat())).fetchone()
            if not row:
                raise ValidationError("Запись не найдена.", 409)
            desired = as_bool(op.get("dismissed"), "dismissed")
            before = op.get("before", {})
            if not isinstance(before, dict) or before.get("status") != row["status"] or before.get("dismissed_at") != row["dismissed_at"]:
                raise ValidationError("Дело изменилось на ноутбуке.", 409)
            try:
                when = datetime.fromisoformat(op["dismissed_at"]) if desired else None
            except (KeyError, ValueError, TypeError) as error:
                raise ValidationError("Некорректное время зачёркивания.") from error
            self.store._dismiss(db, row["id"], desired, when)
            return
        if kind == "complete":
            day = parse_day(op.get("date"))
            if day > today or day < today - timedelta(days=3650):
                raise ValidationError("Дата выполнения вне допустимого диапазона.")
            completed = as_bool(op.get("completed"), "completed")
            self.store._materialize(db, day)
            occurrence = db.execute("SELECT * FROM occurrences WHERE task_id=? AND date=?",
                                    (task_id, day.isoformat())).fetchone()
            if not occurrence or occurrence["status"] == "cancelled":
                raise ValidationError("Запись отменена или больше не входит в расписание.", 409)
            if occurrence["dismissed_at"]:
                raise ValidationError("Сначала верни зачёркнутое дело в работу.", 409)
            desired = "done" if completed else "pending"
            if occurrence["status"] == desired:
                return
            before = op.get("before", {})
            if not isinstance(before, dict):
                raise ValidationError("Некорректная исходная запись выполнения.")
            if (before.get("status") != occurrence["status"] or
                    before.get("completed_at") != occurrence["completed_at"]):
                raise ValidationError("Выполнение изменилось на ноутбуке.", 409)
            completed_at = None
            if completed:
                try:
                    moment = datetime.fromisoformat(op["completed_at"])
                    now = self.store.clock()
                    if moment.tzinfo is None or not now - timedelta(days=3650) <= moment <= now + timedelta(days=1):
                        raise ValueError()
                    completed_at = moment.isoformat()
                except (KeyError, TypeError, ValueError) as error:
                    raise ValidationError("Некорректное время выполнения.") from error
            if not completed and current["archived_date"] and day.isoformat() >= current["archived_date"]:
                desired = "cancelled"
            db.execute("UPDATE occurrences SET status=?,completed_at=?,snooze_until=NULL WHERE id=?",
                       (desired, completed_at, occurrence["id"]))
            db.execute("UPDATE notifications SET read=1 WHERE occurrence_id=?", (occurrence["id"],))
            return
        if current["archived_date"]:
            if kind == "archive":
                return
            raise ValidationError("Задача архивирована на ноутбуке.", 409)
        if kind not in ("edit", "archive"):
            raise ValidationError("Неизвестная команда телефона.")
        values = op.get("values", {}) if kind == "edit" else {}
        before = op.get("before", {})
        if not isinstance(values, dict) or not isinstance(before, dict) or set(values) - set(FIELDS):
            raise ValidationError("Неизвестные поля изменения.")
        compared = values if kind == "edit" else FIELDS
        for key in compared:
            if kind == "archive" and key not in before and key in ("reminder_end_date", "event_date", "escalation_days"):
                if current[key] == (3 if key == "escalation_days" else None):
                    continue
            if key not in before or (current[key] != before[key] and current[key] != values.get(key)):
                raise ValidationError("На двух устройствах изменено одно поле: " + key + ".", 409)
        if kind == "archive":
            archived = parse_day(op.get("date"))
            if archived > today or archived < today - timedelta(days=3650):
                raise ValidationError("Некорректная дата архивации.")
            db.execute("UPDATE tasks SET archived_date=? WHERE id=?", (archived.isoformat(), task_id))
            db.execute("UPDATE occurrences SET status='cancelled' WHERE task_id=? AND date>=? AND status='pending'",
                       (task_id, archived.isoformat()))
            return
        # Правка, сделанная офлайн вчера, не должна отклоняться из-за смены суток.
        start = parse_day(values.get("start_date", current["start_date"]))
        if start < today - timedelta(days=3650):
            raise ValidationError("Дата начала слишком старая.")
        task = dict(current, **clean_task(values, min(today, start), current))
        db.execute("UPDATE tasks SET " + ",".join(key + "=?" for key in FIELDS) + " WHERE id=?",
                   tuple(task[key] for key in FIELDS) + (task_id,))
        rows = db.execute("SELECT * FROM occurrences WHERE task_id=? AND status!='done' "
                          "AND (date>=? OR ?)", (task_id, today.isoformat(), current["recurrence"] == "once"))
        for occurrence in rows.fetchall():
            if occurrence["date"] < today.isoformat():
                continue
            status = "pending" if scheduled(task, parse_day(occurrence["date"])) else "cancelled"
            changed = (task["time"] != occurrence["time"]
                       or task["reminder"] != bool(occurrence["reminder"])
                       or task["recurrence"] != current["recurrence"]
                       or task["start_date"] != current["start_date"]
                       or task["event_date"] != current["event_date"]
                       or task["escalation_days"] != current["escalation_days"])
            db.execute("UPDATE occurrences SET title=?,notes=?,category=?,priority=?,time=?,reminder=?,"
                       "status=?,notified_at=?,snooze_until=? WHERE id=?",
                       tuple(task[key] for key in SNAPSHOT) + (status,
                       None if changed else occurrence["notified_at"],
                       None if changed else occurrence["snooze_until"], occurrence["id"]))
        self.store._materialize(db, today)
        self.store._materialize(db, start)


class SyncHandler(BaseHTTPRequestHandler):
    """Единственная LAN-точка; не публикует локальный UI, файлы или Dictor API."""

    def log_message(self, *_):
        pass  # Не писать коды подключения или содержимое задач в журнал.

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def do_POST(self):
        try:
            address = ipaddress.ip_address(self.client_address[0])
            if not address.is_private or self.headers.get("Origin"):
                raise ValidationError("Доступ разрешён только приложению в локальной сети.", 403)
            if self.path != "/sync/v1":
                raise ValidationError("Неизвестный адрес.", 404)
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + self.server.token).encode()):
                raise ValidationError("Неверный код подключения.", 401)
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                raise ValidationError("Ожидается JSON.", 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValidationError("Некорректный размер запроса.") from error
            if not 1 <= size <= 2_000_000:
                raise ValidationError("Слишком большой запрос.", 413)
            try:
                body = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeDecodeError) as error:
                raise ValidationError("Некорректный JSON.") from error
            if not isinstance(body, dict):
                raise ValidationError("Ожидается объект.")
            self._send(200, self.server.engine.exchange(body))
        except ValidationError as error:
            self._send(error.status, {"error": str(error)})
        except (OSError, TimeoutError):
            pass
        except Exception:
            LOGGER.exception("Ошибка синхронизации")
            self._send(500, {"error": "Ошибка базы ноутбука. См. ritm.log."})

    def _send(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class SyncService:
    """Управлять отдельным LAN-портом без изменения защиты основного сервера."""

    def __init__(self, store, directory):
        self.store, self.path = store, directory / "mobile-sync.json"
        self.lock = threading.RLock()
        self.server = None
        self.discovery = None
        self.error = ""
        self.config = {"enabled": False, "port": 8769,
                       "token": secrets.token_urlsafe(32), "server_id": str(uuid.uuid4())}
        if self.path.exists():
            self.config.update(json.loads(self.path.read_text(encoding="utf-8")))
        self.engine = SyncEngine(store, self.config["server_id"])
        self._save()
        if self.config["enabled"]:
            try:
                self._start()
            except OSError as error:
                self.error = "Не удалось открыть порт синхронизации: " + str(error)

    def _save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def _start(self):
        if self.server:
            return
        server = ThreadingHTTPServer(("0.0.0.0", self.config["port"]), SyncHandler)
        server.daemon_threads = True
        server.token, server.engine = self.config["token"], self.engine
        discovery = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            discovery.bind(("0.0.0.0", server.server_port))
            discovery.settimeout(0.5)
        except OSError:
            discovery.close()
            server.server_close()
            raise
        self.discovery = discovery
        self.server = server
        threading.Thread(target=server.serve_forever, daemon=True, name="ritm-mobile-sync").start()
        threading.Thread(target=self._discover, args=(discovery, server.server_port),
                         daemon=True, name="ritm-discovery").start()

    def _discover(self, listener, port):
        while self.discovery is listener:
            try:
                packet, peer = listener.recvfrom(1024)
                if not ipaddress.ip_address(peer[0]).is_private:
                    continue
                request = json.loads(packet)
                nonce = request.get("nonce", "")
                if (request.get("service") != "ritm-discover-v1"
                        or request.get("server_id") != self.config["server_id"]
                        or not isinstance(nonce, str) or len(nonce) != 32
                        or any(c not in "0123456789abcdef" for c in nonce)):
                    continue
                signed = f"{nonce}|{self.config['server_id']}|{port}"
                proof = hmac.new(self.config["token"].encode(), signed.encode(), hashlib.sha256).hexdigest()
                listener.sendto(json.dumps(dict(service="ritm-discover-v1", nonce=nonce,
                    server_id=self.config["server_id"], port=port, proof=proof)).encode(), peer)
            except (ValueError, AttributeError, TypeError):
                continue
            except socket.timeout:
                continue
            except OSError:
                break

    def close(self):
        with self.lock:
            if self.discovery:
                listener, self.discovery = self.discovery, None
                listener.close()
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                self.server = None

    def configure(self, body):
        with self.lock:
            enabled = as_bool(body.get("enabled", self.config["enabled"]), "enabled")
            rotate = as_bool(body.get("rotate", False), "rotate")
            if rotate or not enabled:
                self.close()
            if rotate:
                self.config["token"] = secrets.token_urlsafe(32)
            self.config["enabled"] = enabled
            self.error = ""
            try:
                if enabled:
                    self._start()
            except OSError as error:
                self.config["enabled"] = False
                self.error = "Порт 8769 занят: " + str(error)
            self._save()
            return self.status()

    def status(self):
        addresses = set()
        try:
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = ipaddress.ip_address(item[4][0])
                if address.is_private and not address.is_loopback:
                    addresses.add(str(address))
        except OSError:
            pass
        return {"enabled": bool(self.server), "error": self.error,
                "addresses": [f"http://{ip}:{self.config['port']}" for ip in sorted(addresses)],
                "token": self.config["token"] if self.server else ""}
