"""SQLite: расписания, отдельные выполнения и история прогресса."""

import hashlib
import calendar
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta

from .config import (
    APPEARANCE_RANGES, CATEGORIES, COLOR_SETTINGS, DEFAULT_SETTINGS, HOUR_INTERVALS,
    PRIORITIES, RECURRENCES, THEMES,
)
from .recurrence import notification_due


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    recurrence TEXT NOT NULL,
    start_date TEXT NOT NULL,
    time TEXT,
    reminder INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    archived_date TEXT
);
CREATE TABLE IF NOT EXISTS occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    date TEXT NOT NULL,
    title TEXT NOT NULL,
    notes TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    time TEXT,
    reminder INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'done', 'cancelled')),
    completed_at TEXT,
    notified_at TEXT,
    snooze_until TEXT,
    UNIQUE(task_id, date)
);
CREATE INDEX IF NOT EXISTS occurrences_date ON occurrences(date);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_id INTEGER REFERENCES occurrences(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    response TEXT NOT NULL
);
"""


class ValidationError(ValueError):
    """Ошибка входных данных с HTTP-статусом."""

    def __init__(self, message, status=400):
        """Сохранить понятное сообщение и статус ответа."""
        super().__init__(message)
        self.status = status


def local_now():
    """Вернуть текущее время с системным часовым поясом."""
    return datetime.now().astimezone()


def parse_day(value):
    """Проверить дату в строгом формате YYYY-MM-DD."""
    if not isinstance(value, str) or len(value) != 10:
        raise ValidationError("Дата должна иметь формат YYYY-MM-DD.")
    try:
        result = date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError("Некорректная дата.") from error
    if result.isoformat() != value:
        raise ValidationError("Дата должна иметь формат YYYY-MM-DD.")
    return result


def as_bool(value, name):
    """Принимать только JSON boolean, без неявных преобразований."""
    if type(value) is not bool:
        raise ValidationError(f"Поле {name} должно быть true или false.")
    return value


def clean_task(payload, today, current=None):
    """Проверить и нормализовать создание или изменение расписания."""
    if not isinstance(payload, dict):
        raise ValidationError("Ожидается JSON-объект задачи.")
    fields = {
        "title", "notes", "category", "priority", "recurrence",
        "start_date", "time", "reminder", "reminder_end_date", "event_date", "escalation_days",
    }
    if set(payload) - fields:
        raise ValidationError("В задаче есть неизвестные поля.")
    result = {
        "title": "", "notes": "", "category": "Личное",
        "priority": "normal", "recurrence": "once",
        "start_date": today.isoformat(), "time": None, "reminder": False,
        "reminder_end_date": None, "event_date": None, "escalation_days": 3,
    }
    if current:
        result.update({key: current[key] for key in fields})
        result["reminder"] = bool(result["reminder"])
    result.update(payload)
    if current and "recurrence" in payload and "reminder_end_date" not in payload:
        if current["recurrence"] == "once" and result["recurrence"] != "once" and current["reminder_end_date"] == current["start_date"]:
            result["reminder_end_date"] = None
    if result["reminder"] is True and not result["time"] and "time" not in payload:
        result["time"] = "09:00"
    for key, maximum in (("title", 200), ("notes", 2000)):
        if not isinstance(result[key], str):
            raise ValidationError(f"Поле {key} должно быть строкой.")
        result[key] = result[key].strip()
        if len(result[key]) > maximum:
            raise ValidationError(f"Поле {key}: максимум {maximum} символов.")
    if not result["title"]:
        raise ValidationError("Напиши название задачи.")
    if result["category"] not in CATEGORIES:
        raise ValidationError("Выбери существующую категорию.")
    if result["priority"] not in PRIORITIES:
        raise ValidationError("Некорректный приоритет.")
    if result["recurrence"] not in RECURRENCES:
        raise ValidationError("Некорректное повторение.")
    start = parse_day(result["start_date"])
    if result["recurrence"] == "once":
        if (not current or current["recurrence"] != "once") and not result["reminder_end_date"]:
            result["reminder_end_date"] = start.isoformat()
        elif current and "reminder_end_date" not in payload and result["start_date"] != current["start_date"] and current["reminder_end_date"] in (None, current["start_date"]):
            result["reminder_end_date"] = start.isoformat()
    changed_start = not current or result["start_date"] != current["start_date"]
    if start < today and changed_start:
        raise ValidationError("Начало новой задачи не может быть в прошлом.")
    if start > today + timedelta(days=3650):
        raise ValidationError("Можно планировать не дальше чем на 10 лет.")
    for key in ("reminder_end_date", "event_date"):
        if result[key] == "":
            result[key] = None
        if result[key] is not None:
            end = parse_day(result[key])
            if end < start or end > today + timedelta(days=3650):
                raise ValidationError("Дата окончания или события должна быть не раньше начала и в пределах 10 лет.")
    if type(result["escalation_days"]) is not int or not 1 <= result["escalation_days"] <= 365:
        raise ValidationError("Учащение: укажи от 1 до 365 дней до события.")
    if result["recurrence"] == "event" and not result["event_date"]:
        raise ValidationError("Укажи дату события.")
    if result["recurrence"] in ("three_times_daily", "event"):
        if not result["reminder"]:
            raise ValidationError("Для этого режима включи напоминание.")
        result["time"] = "09:00"
    value = result["time"]
    if value == "":
        value = None
    if value is not None:
        if not isinstance(value, str) or len(value) != 5:
            raise ValidationError("Время должно иметь формат HH:MM.")
        try:
            parsed = time.fromisoformat(value)
        except ValueError as error:
            raise ValidationError("Некорректное время.") from error
        if parsed.strftime("%H:%M") != value:
            raise ValidationError("Время должно иметь формат HH:MM.")
    result["time"] = value
    as_bool(result["reminder"], "reminder")
    if result["reminder"] and not value:
        raise ValidationError("Для напоминания укажи время.")
    if result["recurrence"] in HOUR_INTERVALS and not result["reminder"]:
        raise ValidationError("Для часового повтора включи напоминание и укажи время.")
    return result


def scheduled(task, day):
    """Определить, входит ли день в расписание задачи."""
    start = date.fromisoformat(task["start_date"])
    archived = task["archived_date"]
    if day < start or (archived and day.isoformat() >= archived):
        return False
    recurrence = task["recurrence"]
    if recurrence == "event":
        return day.isoformat() <= task["event_date"]
    if recurrence == "once":
        return day == start
    if recurrence == "weekdays":
        return day.weekday() < 5
    if recurrence == "weekly":
        return day.weekday() == start.weekday()
    if recurrence == "every_3_days":
        return (day - start).days % 3 == 0
    if recurrence == "monthly":
        return day.day == min(start.day, calendar.monthrange(day.year, day.month)[1])
    return True


class Store:
    """Доступ к базе с отдельным соединением на каждый запрос."""

    def __init__(self, path, clock=local_now):
        """Подготовить базу; clock позволяет проверять смену суток."""
        self.path = path
        self.clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("База создана более новой версией Ритма.")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
            for name, definition in (("reminder_end_date", "TEXT"), ("event_date", "TEXT"),
                                     ("escalation_days", "INTEGER NOT NULL DEFAULT 3")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            connection.execute("PRAGMA user_version = 2")
            for key, value in DEFAULT_SETTINGS.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings VALUES (?, ?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
        self.refresh()

    @contextmanager
    def connection(self, write=False):
        """Завершить транзакцию и закрыть соединение даже при ошибке."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _materialize(self, connection, day):
        """Создать отдельные записи дня без изменения существующей истории."""
        for task in connection.execute("SELECT * FROM tasks").fetchall():
            if scheduled(task, day):
                connection.execute(
                    """INSERT OR IGNORE INTO occurrences
                    (task_id, date, title, notes, category, priority,
                     time, reminder) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task["id"], day.isoformat(), task["title"], task["notes"],
                     task["category"], task["priority"], task["time"],
                     task["reminder"]),
                )

    def refresh(self):
        """Восстановить пропущенные дни после остановки и создать сегодня."""
        today = self.clock().date()
        with self.connection(write=True) as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'last_day'"
            ).fetchone()
            first = date.fromisoformat(json.loads(row[0])) if row else today
            if first > today:
                first = today
            while first <= today:
                self._materialize(connection, first)
                first += timedelta(days=1)
            connection.execute(
                "INSERT OR REPLACE INTO settings VALUES ('last_day', ?)",
                (json.dumps(today.isoformat()),),
            )

    def _request(self, connection, request_id, payload):
        """Вернуть сохранённый ответ на идентичный повтор команды."""
        if request_id is None:
            return None, None
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValidationError("request_id: от 1 до 128 символов.")
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        row = connection.execute(
            "SELECT * FROM requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row and row["fingerprint"] != fingerprint:
            raise ValidationError(
                "request_id уже использован для другой команды.", 409
            )
        return json.loads(row["response"]) if row else None, fingerprint

    def create_task(self, payload, request_id=None):
        """Создать расписание; повтор request_id не создаёт дубликат."""
        self.refresh()
        now = self.clock()
        with self.connection(write=True) as connection:
            cached, fingerprint = self._request(connection, request_id, payload)
            if cached is not None:
                return cached
            task = clean_task(payload, now.date())
            task.update({
                "id": str(uuid.uuid4()), "created_at": now.isoformat(),
                "archived_date": None,
            })
            keys = list(task)
            columns = ", ".join('"' + key + '"' for key in keys)
            marks = ", ".join("?" for _ in keys)
            connection.execute(
                f"INSERT INTO tasks ({columns}) VALUES ({marks})",
                tuple(task[key] for key in keys),
            )
            self._materialize(connection, now.date())
            self._materialize(
                connection, date.fromisoformat(task["start_date"])
            )
            if request_id:
                connection.execute(
                    "INSERT INTO requests VALUES (?, ?, ?)",
                    (request_id, fingerprint, json.dumps(task)),
                )
        return task

    def list_tasks(self):
        """Вернуть активные шаблоны для редактора и Dictor."""
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE archived_date IS NULL "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row, reminder=bool(row["reminder"])) for row in rows]

    def update_task(self, task_id, payload):
        """Обновить будущие ожидания и просроченную разовую задачу."""
        self.refresh()
        today = self.clock().date()
        with self.connection(write=True) as connection:
            current = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND archived_date IS NULL",
                (task_id,),
            ).fetchone()
            if not current:
                raise ValidationError("Задача не найдена.", 404)
            task = dict(current)
            task.update(clean_task(payload, today, task))
            fields = (
                "title", "notes", "category", "priority", "recurrence",
                "start_date", "time", "reminder", "reminder_end_date", "event_date", "escalation_days",
            )
            assignments = ", ".join('"' + key + '" = ?' for key in fields)
            connection.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?",
                tuple(task[key] for key in fields) + (task_id,),
            )
            rows = connection.execute(
                "SELECT * FROM occurrences WHERE task_id = ? "
                "AND (date >= ? OR ?) AND status != 'done'",
                (task_id, today.isoformat(), current["recurrence"] == "once"),
            ).fetchall()
            snapshot = ("title", "notes", "category", "priority", "time",
                        "reminder")
            for row in rows:
                if row["date"] < today.isoformat():
                    continue  # Прошлый план остаётся в истории даже после переноса задачи.
                status = (
                    "pending" if scheduled(task, parse_day(row["date"]))
                    else "cancelled"
                )
                changed_time = (
                    task["time"] != row["time"]
                    or bool(task["reminder"]) != bool(row["reminder"])
                    or task["recurrence"] != current["recurrence"]
                    or task["start_date"] != current["start_date"]
                    or task["event_date"] != current["event_date"]
                    or task["escalation_days"] != current["escalation_days"]
                )
                connection.execute(
                    "UPDATE occurrences SET title=?, notes=?, category=?, "
                    "priority=?, time=?, reminder=?, status=?, notified_at=?, "
                    "snooze_until=? WHERE id=?",
                    tuple(task[key] for key in snapshot) + (
                        status, None if changed_time else row["notified_at"],
                        None if changed_time else row["snooze_until"], row["id"],
                    ),
                )
            self._materialize(connection, today)
            self._materialize(connection, parse_day(task["start_date"]))
        return dict(task, reminder=bool(task["reminder"]))

    def archive_task(self, task_id):
        """Скрыть расписание и будущие ожидания, сохранив выполнения."""
        self.refresh()
        today = self.clock().date().isoformat()
        with self.connection(write=True) as connection:
            changed = connection.execute(
                "UPDATE tasks SET archived_date=? WHERE id=? "
                "AND archived_date IS NULL", (today, task_id),
            ).rowcount
            if not changed:
                raise ValidationError("Задача не найдена.", 404)
            connection.execute(
                "UPDATE occurrences SET status='cancelled' WHERE task_id=? "
                "AND date>=? AND status='pending'", (task_id, today),
            )
            connection.execute(
                "UPDATE notifications SET read=1 WHERE occurrence_id IN "
                "(SELECT id FROM occurrences WHERE task_id=?)",
                (task_id,),
            )
        return {"archived": True}

    def complete(self, occurrence_id, completed=True):
        """Установить выполнение идемпотентно, без повторных очков."""
        as_bool(completed, "completed")
        now = self.clock()
        with self.connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM occurrences WHERE id=?", (occurrence_id,)
            ).fetchone()
            if not row or row["status"] == "cancelled":
                raise ValidationError("Запись задачи не найдена.", 404)
            if parse_day(row["date"]) > now.date():
                raise ValidationError("Будущие задачи пока нельзя выполнять.")
            if row["status"] == ("done" if completed else "pending"):
                return {"completed": completed, "id": occurrence_id}
            target_status = "done" if completed else "pending"
            if not completed:
                task = connection.execute(
                    "SELECT archived_date FROM tasks WHERE id=?",
                    (row["task_id"],),
                ).fetchone()
                if task["archived_date"] and row["date"] >= task["archived_date"]:
                    target_status = "cancelled"
            connection.execute(
                "UPDATE occurrences SET status=?, completed_at=?, "
                "snooze_until=NULL WHERE id=?",
                (target_status,
                 now.isoformat() if completed else None, occurrence_id),
            )
            connection.execute(
                "UPDATE notifications SET read=1 WHERE occurrence_id=?",
                (occurrence_id,),
            )
        return {"completed": completed, "id": occurrence_id}

    def snooze(self, occurrence_id, minutes):
        """Отложить ожидающую задачу на заданное число минут."""
        if type(minutes) is not int or minutes not in (5, 10, 15, 30, 60):
            raise ValidationError("Можно отложить на 5, 10, 15, 30 или 60 минут.")
        until = self.clock() + timedelta(minutes=minutes)
        with self.connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM occurrences WHERE id=? AND status='pending'",
                (occurrence_id,),
            ).fetchone()
            if not row or row["date"] > self.clock().date().isoformat():
                raise ValidationError("Нет активного напоминания.")
            if not row["reminder"]:
                raise ValidationError("Сначала включи напоминание задачи.")
            connection.execute(
                "UPDATE occurrences SET snooze_until=?, notified_at=NULL "
                "WHERE id=?", (until.isoformat(), occurrence_id),
            )
            connection.execute(
                "UPDATE notifications SET read=1 WHERE occurrence_id=?",
                (occurrence_id,),
            )
        return {"snooze_until": until.isoformat()}

    def settings(self):
        """Вернуть пользовательские настройки без внутренних ключей."""
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM settings").fetchall()
        values = {row["key"]: json.loads(row["value"]) for row in rows}
        return {key: values.get(key, default)
                for key, default in DEFAULT_SETTINGS.items()}

    def update_settings(self, payload):
        """Проверить и сохранить известные настройки."""
        if not isinstance(payload, dict) or set(payload) - DEFAULT_SETTINGS.keys():
            raise ValidationError("Неизвестные поля настроек.")
        for key, value in payload.items():
            if key == "display_name":
                if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40:
                    raise ValidationError("Имя: от 1 до 40 символов.")
            elif key == "theme":
                if not isinstance(value, str) or value not in THEMES:
                    raise ValidationError("Выбери существующую тему.")
            elif key in COLOR_SETTINGS and value is None:
                continue
            elif key == "accent_color" or key in COLOR_SETTINGS:
                if not isinstance(value, str) or not re.fullmatch(
                    r"#[0-9a-fA-F]{6}", value
                ):
                    raise ValidationError("Цвет должен иметь формат #RRGGBB.")
            elif key in APPEARANCE_RANGES:
                minimum, maximum = APPEARANCE_RANGES[key]
                if type(value) is not int or not minimum <= value <= maximum:
                    raise ValidationError(
                        f"Поле {key}: от {minimum} до {maximum}."
                    )
            else:
                as_bool(value, key)
        with self.connection(write=True) as connection:
            for key, value in payload.items():
                if (key == "accent_color" or key in COLOR_SETTINGS) and value:
                    value = value.lower()
                connection.execute(
                    "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                    (key, json.dumps(value.strip() if isinstance(value, str)
                                     else value, ensure_ascii=False)),
                )
        return self.settings()

    def due_notifications(self):
        """Зафиксировать наступившие напоминания ровно один раз."""
        self.refresh()
        now = self.clock()
        today = now.date().isoformat()
        result = []
        if not self.settings()["notifications"]:
            return result
        with self.connection(write=True) as connection:
            rows = connection.execute(
                """SELECT o.*, t.recurrence, t.start_date, t.reminder_end_date,
                t.event_date, t.escalation_days FROM occurrences o
                JOIN tasks t ON t.id=o.task_id
                WHERE o.reminder=1 AND o.status='pending'
                AND t.archived_date IS NULL
                AND o.date<=?
                AND (o.date=? OR t.recurrence='once'
                     OR o.snooze_until IS NOT NULL)
                ORDER BY o.date, o.time""", (today, today),
            ).fetchall()
            for row in rows:
                if not notification_due(row, now):
                    continue
                title = row["title"]
                body = "Время сделать шаг вперёд. Открой Ритм и отметь задачу."
                cursor = connection.execute(
                    "INSERT INTO notifications "
                    "(occurrence_id, title, body, created_at) VALUES (?,?,?,?)",
                    (row["id"], title, body, now.isoformat()),
                )
                connection.execute(
                    "UPDATE occurrences SET notified_at=?,snooze_until=NULL WHERE id=?",
                    (now.isoformat(), row["id"]),
                )
                result.append({"id": cursor.lastrowid, "title": title,
                               "body": body, "occurrence_id": row["id"]})
        return result

    def test_notification(self):
        """Добавить проверочное напоминание в общий журнал."""
        item = {"title": "Ритм на связи", "body": "Напоминания готовы к работе."}
        with self.connection(write=True) as connection:
            cursor = connection.execute(
                "INSERT INTO notifications (title,body,created_at) VALUES (?,?,?)",
                (item["title"], item["body"], self.clock().isoformat()),
            )
        return dict(item, id=cursor.lastrowid)

    def read_notifications(self, ids):
        """Отметить конкретные уведомления прочитанными."""
        if not isinstance(ids, list) or len(ids) > 500:
            raise ValidationError("ids: список не более 500 идентификаторов.")
        if any(type(item) is not int or item <= 0 for item in ids):
            raise ValidationError("Идентификаторы должны быть целыми числами.")
        with self.connection(write=True) as connection:
            connection.executemany(
                "UPDATE notifications SET read=1 WHERE id=?",
                [(item,) for item in ids],
            )
        return {"read": len(ids)}

    def integration_seen(self):
        """Записать время принятой команды Dictor."""
        with self.connection(write=True) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings VALUES ('dictor_last_seen', ?)",
                (json.dumps(self.clock().isoformat()),),
            )

    def state(self, selected):
        """Собрать задачи дня, фактический прогресс и журнал уведомлений."""
        day = parse_day(selected)
        today = self.clock().date()
        if abs((day - today).days) > 3650:
            raise ValidationError("Выбери дату в пределах 10 лет.")
        self.refresh()
        monday = day - timedelta(days=day.weekday())
        with self.connection(write=True) as connection:
            if day >= today:
                self._materialize(connection, day)
            for offset in range(7):
                week_day = monday + timedelta(days=offset)
                if week_day >= today:
                    self._materialize(connection, week_day)
            rows = connection.execute(
                "SELECT o.*, t.recurrence, t.archived_date, t.start_date, t.reminder_end_date FROM occurrences o "
                "JOIN tasks t ON t.id=o.task_id WHERE o.status!='cancelled' "
                "AND o.date=? "
                "ORDER BY o.date, o.time IS NULL, o.time, o.id",
                (selected,),
            ).fetchall()
            unfinished = connection.execute(
                "SELECT o.*, t.recurrence, t.archived_date, t.start_date, t.reminder_end_date FROM occurrences o "
                "JOIN tasks t ON t.id=o.task_id WHERE o.status='pending' AND o.date<? "
                "ORDER BY o.date DESC, o.time IS NULL, o.time, o.id",
                (today.isoformat(),),
            ).fetchall()
            counts = connection.execute(
                "SELECT date, COUNT(*) AS total, "
                "SUM(status='done') AS done FROM occurrences "
                "WHERE status!='cancelled' GROUP BY date"
            ).fetchall()
            history = connection.execute(
                "SELECT id,task_id,date,title,category,completed_at "
                "FROM occurrences WHERE status='done' "
                "ORDER BY completed_at DESC LIMIT 100"
            ).fetchall()
            completed_counts = connection.execute(
                "SELECT substr(completed_at,1,10) AS date, COUNT(*) AS done "
                "FROM occurrences WHERE status='done' GROUP BY 1"
            ).fetchall()
            missed_counts = connection.execute(
                "SELECT date, COUNT(*) AS missed FROM occurrences WHERE status!='cancelled' "
                "AND (status='pending' OR substr(completed_at,1,10)>date) GROUP BY date"
            ).fetchall()
            notifications = connection.execute(
                "SELECT n.*, CASE WHEN t.archived_date IS NOT NULL "
                "AND o.status='pending' THEN 'cancelled' ELSE o.status "
                "END AS task_status FROM notifications n "
                "LEFT JOIN occurrences o ON o.id=n.occurrence_id "
                "LEFT JOIN tasks t ON t.id=o.task_id "
                "ORDER BY n.id DESC LIMIT 100"
            ).fetchall()
            seen = connection.execute(
                "SELECT value FROM settings WHERE key='dictor_last_seen'"
            ).fetchone()
        by_day = {row["date"]: dict(row) for row in counts}
        completed_days = {row["date"]: row["done"] for row in completed_counts}
        missed_days = {row["date"]: row["missed"] for row in missed_counts}
        done_dates = {parse_day(key) for key in completed_days
                      if parse_day(key) <= today}
        current = today if today in done_dates else today - timedelta(days=1)
        streak = 0
        while current in done_dates:
            streak += 1
            current -= timedelta(days=1)
        longest = run = 0
        previous = None
        for active in sorted(done_dates):
            run = run + 1 if previous == active - timedelta(days=1) else 1
            longest = max(longest, run)
            previous = active
        total_done = sum(item["done"] for item in by_day.values())
        week = []
        for offset in range(7):
            key = (monday + timedelta(days=offset)).isoformat()
            week.append(by_day.get(key, {"date": key, "total": 0, "done": 0}))
        activity = []
        for offset in range(90, -1, -1):
            key = (today - timedelta(days=offset)).isoformat()
            activity.append({"date": key, "done": completed_days.get(key, 0),
                             "unfinished": missed_days.get(key, 0)})
        return {
            "today": today.isoformat(), "selected_date": selected,
            "server_time": self.clock().isoformat(),
            "tasks": [dict(row, reminder=bool(row["reminder"])) for row in rows],
            "unfinished": [dict(row, reminder=bool(row["reminder"])) for row in unfinished],
            "week": week, "activity": activity,
            "summary": {"done": total_done, "xp": total_done * 10,
                        "level": total_done // 10 + 1,
                        "active_days": len(done_dates), "streak": streak,
                        "longest_streak": longest},
            "history": [dict(row) for row in history],
            "notifications": [dict(row) for row in notifications],
            "settings": self.settings(),
            "dictor_last_seen": json.loads(seen[0]) if seen else None,
            "has_tasks": bool(self.list_tasks()),
        }

    def day_details(self, selected):
        """Фактические выполнения и план, не законченный к концу выбранного дня."""
        day = parse_day(selected)
        today = self.clock().date()
        if abs((day - today).days) > 3650:
            raise ValidationError("Выбери дату в пределах 10 лет.")
        self.refresh()
        with self.connection() as connection:
            done = connection.execute(
                "SELECT id,task_id,date,title,category,status,completed_at FROM occurrences "
                "WHERE status='done' AND substr(completed_at,1,10)=? ORDER BY completed_at,id",
                (selected,),
            ).fetchall()
            unfinished = connection.execute(
                "SELECT id,task_id,date,title,category,status,completed_at FROM occurrences "
                "WHERE date=? AND status!='cancelled' "
                "AND (status='pending' OR substr(completed_at,1,10)>?) ORDER BY time IS NULL,time,id",
                (selected, selected),
            ).fetchall()
        return {"date": selected, "is_past": day < today,
                "done": [dict(row) for row in done],
                "unfinished": [dict(row) for row in unfinished]}

    def calendar(self, month):
        """Собрать шесть календарных недель с сохранёнными задачами."""
        if not isinstance(month, str) or len(month) != 7:
            raise ValidationError("Месяц должен иметь формат YYYY-MM.")
        first = parse_day(month + "-01")
        today = self.clock().date()
        if abs((first - today).days) > 3650:
            raise ValidationError("Выбери месяц в пределах 10 лет.")
        start = first - timedelta(days=first.weekday())
        end = start + timedelta(days=41)
        self.refresh()
        with self.connection(write=True) as connection:
            for offset in range(42):
                day = start + timedelta(days=offset)
                if day >= today:
                    self._materialize(connection, day)
            rows = connection.execute(
                "SELECT id,date,title,category,time,status FROM occurrences "
                "WHERE date>=? AND date<=? AND status!='cancelled' "
                "ORDER BY date,time IS NULL,time,id",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        grouped = {}
        for row in rows:
            grouped.setdefault(row["date"], []).append(dict(row))
        return {"month": month, "days": [
            {"date": (start + timedelta(days=offset)).isoformat(),
             "tasks": grouped.get(
                 (start + timedelta(days=offset)).isoformat(), []
             )} for offset in range(42)
        ]}

    def export(self):
        """Сформировать переносимый экспорт без ключей API."""
        with self.connection() as connection:
            tables = {
                name: [dict(row) for row in connection.execute(f"SELECT * FROM {name}")]
                for name in ("tasks", "occurrences", "notifications")
            }
        return dict(tables, schema_version=1, app="ritm",
                    exported_at=self.clock().isoformat(),
                    settings={key: value for key, value in self.settings().items()
                              if key != "autostart"})
