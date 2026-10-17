"""Локальный HTTP API и интерфейс; доступ только с loopback."""

import hmac
import json
import logging
import re
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .store import ValidationError, as_bool
from .assistant_plan import preview_plan, import_plan


LOGGER = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).parent / "web"
WEB_FILES = (
    "index.html", "styles.css", "themes.css", "theme.js", "app.js",
    "icon.svg", "icon.ico", "icon.png", "companions.js",
)
MAX_BODY = 65536
TASK_PATH = re.compile(r"/api/v1/tasks/([a-f0-9-]{36})$")
OCCURRENCE_PATH = re.compile(
    r"/api/v1/occurrences/([1-9][0-9]*)/(complete|snooze|dismiss)$"
)


def load_web_assets():
    """Загрузить согласованный комплект UI, объединив стили и скрипты."""
    assets = {}
    for name in WEB_FILES:
        path = WEB_ROOT / name
        if not path.is_file():
            raise RuntimeError(
                f"Отсутствует файл интерфейса {name}. "
                "Распакуй обновление полностью и пересобери приложение."
            )
        assets[name] = path.read_bytes()
        if not assets[name]:
            raise RuntimeError(f"Пустой файл интерфейса: {name}")
    assets["styles.css"] += b"\n" + assets["themes.css"]
    assets["app.js"] = assets["theme.js"] + b"\n;\n" + assets["app.js"]
    assets["app.js"] += b"\n;\n" + assets["companions.js"]
    return assets


class LocalServer(ThreadingHTTPServer):
    """Сервер с явным владением хранилищем и системными действиями."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, store, token, session, actions):
        """Привязать приложение только к IPv4 loopback."""
        if address[0] != "127.0.0.1":
            raise ValueError("Разрешён только адрес 127.0.0.1.")
        self.store = store
        self.token = token
        self.session = session
        self.actions = actions
        self.assets = load_web_assets()
        super().__init__(address, Handler)

    @property
    def base_url(self):
        """Вернуть origin фактического порта."""
        return f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    """Обработчик API с токеном, строгим Origin и ограничением тела."""

    server_version = "Ritm"
    sys_version = ""

    def setup(self):
        """Ограничить время чтения незавершённого запроса."""
        super().setup()
        self.connection.settimeout(8)

    def log_message(self, format_string, *args):
        """Не записывать URL запуска и секреты в HTTP-журнал."""
        return

    def _send(self, code, body, content_type="application/json", headers=None):
        """Отправить ответ с закрытым кэшем и защитными заголовками."""
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _check_origin(self):
        """Запретить чужой Host, cross-origin запросы и DNS rebinding."""
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origins = {f"http://{host}" for host in hosts}
        if self.headers.get("Host") not in hosts:
            raise ValidationError("Недопустимый Host.", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in origins:
            raise ValidationError("Cross-origin доступ запрещён.", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ValidationError("Cross-site доступ запрещён.", 403)

    def _authorized(self):
        """Проверить отдельный bearer Dictor или HttpOnly-сессию UI."""
        bearer = self.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            return hmac.compare_digest(
                bearer[7:].encode("utf-8"), self.server.token.encode("utf-8")
            )
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        value = cookie.get("ritm_session")
        return bool(value and hmac.compare_digest(
            value.value.encode("utf-8"), self.server.session.encode("utf-8")
        ))

    def _body(self):
        """Прочитать небольшой JSON-объект без chunked-передачи."""
        if self.headers.get("Transfer-Encoding"):
            raise ValidationError("Chunked-запросы не поддерживаются.")
        if self.headers.get_content_type() != "application/json":
            raise ValidationError("Требуется Content-Type: application/json.", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValidationError("Некорректный Content-Length.") from error
        if length < 1 or length > MAX_BODY:
            raise ValidationError("Размер JSON: от 1 до 65536 байт.", 413)
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValidationError("Не удалось прочитать JSON.") from error
        if not isinstance(body, dict):
            raise ValidationError("В корне JSON должен быть объект.")
        return body

    def _dispatch(self):
        """Маршрутизировать запрос и скрыть внутренние ошибки от клиента."""
        try:
            self._check_origin()
            path = urlsplit(self.path).path
            if self.command in ("GET", "HEAD"):
                if path == "/api/v1/health":
                    return self._send(200, {
                        "app": "ritm", "version": __version__, "status": "ok"
                    })
                if path.startswith("/open/"):
                    candidate = path[len("/open/"):]
                    if not hmac.compare_digest(
                        candidate.encode("utf-8"),
                        self.server.session.encode("utf-8"),
                    ):
                        raise ValidationError("Ссылка запуска устарела.", 403)
                    return self._send(303, b"", headers={
                        "Location": "/",
                        "Set-Cookie": "ritm_session=" + self.server.session
                        + "; HttpOnly; SameSite=Strict; Path=/",
                    })
                if not path.startswith("/api/"):
                    return self._static(path)
            if not self._authorized():
                raise ValidationError(
                    "Сессия истекла. Открой Ритм через START_WINDOWS.cmd "
                    "или значок в трее.", 401
                )
            if self.command in ("GET", "HEAD"):
                return self._get(path)
            body = self._body()
            return self._mutate(path, body)
        except ValidationError as error:
            self._send(error.status, {"error": str(error)})
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            return
        except Exception:
            LOGGER.exception("Ошибка обработки %s API", self.command)
            self._send(500, {"error": "Внутренняя ошибка. Подробности в ritm.log."})

    def _static(self, path):
        """Отдавать только известные статические файлы приложения."""
        files = {
            "/": "index.html", "/index.html": "index.html",
            "/styles.css": "styles.css", "/app.js": "app.js",
            "/theme.js": "theme.js",
            "/themes.css": "themes.css",
            "/companions.js": "companions.js",
            "/icon.svg": "icon.svg", "/icon.png": "icon.png", "/favicon.ico": "icon.ico",
        }
        if path not in files:
            raise ValidationError("Страница не найдена.", 404)
        target = WEB_ROOT / files[path]
        types = {
            ".html": "text/html", ".css": "text/css",
            ".js": "text/javascript", ".svg": "image/svg+xml",
            ".ico": "image/x-icon", ".png": "image/png",
        }
        content_type = types[target.suffix]
        return self._send(200, self.server.assets[files[path]], content_type)

    def _get(self, path):
        """Прочитать состояние, шаблоны, экспорт или настройки интеграции."""
        store = self.server.store
        if path == "/api/v1/mobile":
            return self._send(200, self.server.actions["mobile_status"]())
        if path == "/api/v1/state":
            query = parse_qs(urlsplit(self.path).query)
            selected = query.get("date", [store.clock().date().isoformat()])[0]
            state = store.state(selected)
            state["runtime"] = self.server.actions["status"]()
            state["version"] = __version__
            return self._send(200, state)
        if path == "/api/v1/tasks":
            return self._send(200, {"tasks": store.list_tasks()})
        if path == "/api/v1/progress/day":
            query = parse_qs(urlsplit(self.path).query)
            return self._send(200, store.day_details(query.get("date", [store.clock().date().isoformat()])[0]))
        if path == "/api/v1/calendar":
            query = parse_qs(urlsplit(self.path).query)
            month = query.get("month", [store.clock().strftime("%Y-%m")])[0]
            return self._send(200, store.calendar(month))
        if path == "/api/v1/integration":
            return self._send(200, {
                "base_url": self.server.base_url,
                "endpoint": self.server.base_url + "/api/v1/commands",
                "token": self.server.token,
            })
        if path == "/api/v1/export":
            return self._send(200, store.export(), headers={
                "Content-Disposition": 'attachment; filename="ritm-backup.json"'
            })
        raise ValidationError("Метод API не найден.", 404)

    def _mutate(self, path, body):
        """Выполнить явную команду приложения."""
        store = self.server.store
        method = self.command
        if path == "/api/v1/app/exit-fullscreen" and method == "POST":
            return self._send(200, self.server.actions["exit_fullscreen"]())
        if path == "/api/v1/mobile" and method == "POST":
            return self._send(200, self.server.actions["mobile_configure"](body))
        if path == "/api/v1/assistant/preview" and method == "POST":
            return self._send(200, {"tasks": preview_plan(store, body)})
        if path == "/api/v1/assistant/import" and method == "POST":
            return self._send(201, import_plan(store, body))
        if path == "/api/v1/tasks" and method == "POST":
            task = store.create_task(body, self.headers.get("Idempotency-Key"))
            return self._send(201, task)
        task_match = TASK_PATH.fullmatch(path)
        if task_match:
            task_id = task_match[1]
            if method == "PATCH":
                return self._send(200, store.update_task(task_id, body))
            if method == "DELETE":
                return self._send(200, store.archive_task(task_id))
        occurrence_match = OCCURRENCE_PATH.fullmatch(path)
        if occurrence_match and method == "POST":
            occurrence_id = int(occurrence_match[1])
            if occurrence_id > 2**63 - 1:
                raise ValidationError("Некорректный идентификатор.")
            if occurrence_match[2] == "complete":
                result = store.complete(occurrence_id, body.get("completed", True))
            elif occurrence_match[2] == "dismiss":
                result = store.dismiss(occurrence_id, body.get("dismissed", True))
            else:
                result = store.snooze(occurrence_id, body.get("minutes", 10))
            return self._send(200, result)
        if path == "/api/v1/settings" and method == "PATCH":
            if "autostart" in body:
                as_bool(body["autostart"], "autostart")
                # Validate the full payload before any OS setting changes.
                original = store.settings()
                store.update_settings(body)
                try:
                    self.server.actions["autostart"](body["autostart"])
                except Exception:
                    store.update_settings(original)
                    raise
            else:
                store.update_settings(body)
            return self._send(200, store.settings())
        if path == "/api/v1/notifications/read" and method == "POST":
            return self._send(200, store.read_notifications(body.get("ids")))
        if path == "/api/v1/notification-test" and method == "POST":
            item = store.test_notification()
            item["delivery"] = self.server.actions["notify"](item["title"], item["body"])
            return self._send(200, item)
        if path == "/api/v1/commands" and method == "POST":
            return self._command(body)
        if path == "/api/v1/demo" and method == "POST":
            return self._demo()
        if path == "/api/v1/app/quit" and method == "POST":
            self._send(200, {"stopping": True})
            threading.Thread(
                target=self.server.actions["quit"], daemon=True
            ).start()
            return
        raise ValidationError("Метод API не найден.", 404)

    def _command(self, body):
        """Принимать от Dictor только поддержанные структурные действия."""
        store = self.server.store
        action = body.get("action")
        if action == "task.create":
            if set(body) - {"action", "request_id", "task"}:
                raise ValidationError("Неизвестные поля команды.")
            if not body.get("request_id"):
                raise ValidationError("Для Dictor нужен стабильный request_id.")
            task = store.create_task(body.get("task"), body["request_id"])
            result = {"ok": True, "action": action, "task": task}
        elif action == "task.complete":
            if set(body) - {"action", "occurrence_id", "completed"}:
                raise ValidationError("Неизвестные поля команды.")
            occurrence_id = body.get("occurrence_id")
            if type(occurrence_id) is not int or not 1 <= occurrence_id < 2**63:
                raise ValidationError("Укажи occurrence_id из /api/v1/state.")
            result = dict(store.complete(
                occurrence_id, body.get("completed", True)
            ), ok=True, action=action)
        else:
            raise ValidationError(
                "Поддержаны task.create и task.complete. "
                "Распознавание речи и намерения выполняет Dictor."
            )
        store.integration_seen()
        return self._send(200, result)

    def _demo(self):
        """Создать примеры по запросу без выдуманной истории."""
        store = self.server.store
        if store.list_tasks():
            raise ValidationError("Примеры доступны в пустом списке.", 409)
        request_group = store.clock().isoformat()
        samples = (
            ("Сфокусироваться на главной задаче", "Работа", "high", "10:00"),
            ("30 минут машинного обучения", "Учёба", "normal", "18:00"),
            ("Выйти на прогулку", "Здоровье", "normal", "19:00"),
            ("Прочитать 10 страниц", "Личное", "low", "21:00"),
        )
        for index, (title, category, priority, clock) in enumerate(samples):
            store.create_task({
                "title": title, "category": category, "priority": priority,
                "time": clock, "recurrence": "daily",
            }, request_id=f"demo-{request_group}-{index}")
        return self._send(201, {"created": len(samples)})

    def do_GET(self):
        """Обработать чтение."""
        self._dispatch()

    def do_HEAD(self):
        """Проверить ресурс без передачи тела."""
        self._dispatch()

    def do_POST(self):
        """Обработать явную команду."""
        self._dispatch()

    def do_PATCH(self):
        """Обработать изменение."""
        self._dispatch()

    def do_DELETE(self):
        """Обработать архивирование."""
        self._dispatch()

    def do_OPTIONS(self):
        """Не разрешать browser CORS preflight."""
        self._send(403, {"error": "Cross-origin доступ запрещён."})
