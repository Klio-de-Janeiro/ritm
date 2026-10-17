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
from .recurrence import notification_due, grace_due, occurrence_state, deadline, missed_deadline
from .checkins import plan as checkin_plan, decorate as decorate_checks, unpack, events as completion_events


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
        "start_date", "time", "reminder", "reminder_end_date", "event_date", "escalation_days", "repeat_until",
    }
    if set(payload) - fields:
        raise ValidationError("В задаче есть неизвестные поля.")
    result = {
        "title": "", "notes": "", "category": "Личное",
        "priority": "normal", "recurrence": "once",
        "start_date": today.isoformat(), "time": None, "reminder": False,
        "reminder_end_date": None, "event_date": None, "escalation_days": 3, "repeat_until": None,
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
        if key == "title" and result[key]:
            result[key] = result[key][0].upper() + result[key][1:]
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
    if result["recurrence"] in HOUR_INTERVALS:
        end = result["repeat_until"] or max(value or "09:00", "21:00")
        if not isinstance(end, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", end) or end < (value or "09:00"):
            raise ValidationError("Конец повторений должен быть не раньше начала, в формате HH:MM.")
        result["repeat_until"] = end
    else:
        result["repeat_until"] = None
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
            if version > 4:
                raise RuntimeError("База создана более новой версией Ритма.")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
            for name, definition in (("reminder_end_date", "TEXT"), ("event_date", "TEXT"),
                                     ("escalation_days", "INTEGER NOT NULL DEFAULT 3"), ("repeat_until", "TEXT")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            occurrence_columns = {row["name"] for row in connection.execute("PRAGMA table_info(occurrences)")}
            for name in ("dismissed_at", "grace_notified_at", "slots", "checks"):
                if name not in occurrence_columns:
                    connection.execute(f"ALTER TABLE occurrences ADD COLUMN {name} TEXT")
            for row in connection.execute("SELECT o.id,o.time,t.recurrence,t.repeat_until FROM occurrences o JOIN tasks t ON t.id=o.task_id WHERE o.slots IS NULL AND o.status='pending'").fetchall():
                slots = checkin_plan(dict(row))
                if slots:
                    connection.execute("UPDATE occurrences SET slots=?,checks='{}' WHERE id=?", (json.dumps(slots), row["id"]))
            connection.execute("PRAGMA user_version = 4")
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
                    time, reminder, slots, checks) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')""",
                    (task["id"], day.isoformat(), task["title"], task["notes"],
                     task["category"], task["priority"], task["time"],
                     task["reminder"], json.dumps(checkin_plan(dict(task))) if checkin_plan(dict(task)) else None),
                )

    def _init_checkins(self, connection):
        for row in connection.execute("SELECT o.id,o.time,t.recurrence,t.repeat_until FROM occurrences o JOIN tasks t ON t.id=o.task_id WHERE o.slots IS NULL AND o.status='pending'").fetchall():
            slots = checkin_plan(dict(row))
            if slots:
                connection.execute("UPDATE occurrences SET slots=?,checks='{}' WHERE id=?",(json.dumps(slots),row["id"]))

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
                if unpack(row)["checks"]:
                    continue  # Уже отмеченные подходы дня сохраняют своё расписание.
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
                slots = checkin_plan(task)
                connection.execute("UPDATE occurrences SET slots=?,checks='{}' WHERE id=?", (json.dumps(slots) if slots else None, row["id"]))
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
            if row["dismissed_at"]:
                raise ValidationError("Сначала верни зачёркнутое дело в работу.")
            if parse_day(row["date"]) > now.date():
                raise ValidationError("Будущие задачи пока нельзя выполнять.")
            slots = unpack(row)["slots"]
            if slots:
                past = [value for value in slots if datetime.fromisoformat(row["date"]+"T"+value) <= now.replace(tzinfo=None)]
                if not past:
                    raise ValidationError("Первый подход ещё не наступил.")
                return self._check(connection, occurrence_id, past[-1], completed)
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

    def _check(self, connection, occurrence_id, slot, completed, when=None):
        as_bool(completed,"completed")
        row = connection.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
        if not row or row["status"] == "cancelled" or row["dismissed_at"]:
            raise ValidationError("Сначала верни дело в работу.",409)
        data = unpack(row)
        if not data["slots"] or slot not in data["slots"]:
            raise ValidationError("Такого подхода нет в расписании.")
        now = self.clock()
        if datetime.fromisoformat(row["date"]+"T"+slot) > now.replace(tzinfo=None):
            raise ValidationError("Будущий подход пока нельзя отметить.")
        checks = data["checks"]
        if completed:
            moment = when or now
            if moment.tzinfo is None or moment > now + timedelta(minutes=5) or moment.replace(tzinfo=None) < datetime.fromisoformat(row["date"]+"T"+slot):
                raise ValidationError("Некорректное время подхода.")
            checks.setdefault(slot,moment.isoformat())
        else:
            checks.pop(slot,None)
        done = len(checks) == len(data["slots"])
        connection.execute("UPDATE occurrences SET checks=?,status=?,completed_at=?,snooze_until=NULL WHERE id=?",
            (json.dumps(checks),"done" if done else "pending",max(checks.values()) if done else None,occurrence_id))
        return {"id":occurrence_id,"time":slot,"completed":completed}

    def check(self, occurrence_id, slot, completed=True):
        with self.connection(write=True) as connection:
            return self._check(connection,occurrence_id,slot,completed)

    def _dismiss(self, connection, occurrence_id, dismissed, when=None):
        as_bool(dismissed, "dismissed")
        row = connection.execute("SELECT o.*,t.recurrence,t.start_date,t.reminder_end_date FROM occurrences o "
            "JOIN tasks t ON t.id=o.task_id WHERE o.id=?", (occurrence_id,)).fetchone()
        if not row or row["status"] != "pending":
            raise ValidationError("Можно зачеркнуть только невыполненное дело.", 409)
        if bool(row["dismissed_at"]) == dismissed:
            return {"id": occurrence_id, "dismissed_at": row["dismissed_at"]}
        now = self.clock()
        until = deadline(row) + timedelta(hours=2)
        if dismissed and now.replace(tzinfo=None) < until:
            raise ValidationError("Зачеркнуть дело можно после двух дополнительных часов.", 409)
        moment = when or now
        if dismissed and (moment.tzinfo is None or moment.replace(tzinfo=None) < until or moment > now + timedelta(days=1)):
            raise ValidationError("Некорректное время зачёркивания.")
        value = moment.isoformat() if dismissed else None
        connection.execute("UPDATE occurrences SET dismissed_at=?,snooze_until=NULL WHERE id=?", (value, occurrence_id))
        connection.execute("UPDATE notifications SET read=1 WHERE occurrence_id=?", (occurrence_id,))
        return {"id": occurrence_id, "dismissed_at": value}

    def dismiss(self, occurrence_id, dismissed=True):
        with self.connection(write=True) as connection:
            return self._dismiss(connection, occurrence_id, dismissed)

    def snooze(self, occurrence_id, minutes):
        """Отложить ожидающую задачу на заданное число минут."""
        if type(minutes) is not int or minutes not in (5, 10, 15, 30, 60):
            raise ValidationError("Можно отложить на 5, 10, 15, 30 или 60 минут.")
        until = self.clock() + timedelta(minutes=minutes)
        with self.connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM occurrences WHERE id=? AND status='pending' AND dismissed_at IS NULL",
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
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        result = []
        if not self.settings()["notifications"]:
            return result
        with self.connection(write=True) as connection:
            rows = connection.execute(
                """SELECT o.*, t.recurrence, t.start_date, t.reminder_end_date,
                t.event_date, t.escalation_days FROM occurrences o
                JOIN tasks t ON t.id=o.task_id
                WHERE o.status='pending' AND o.dismissed_at IS NULL
                AND t.archived_date IS NULL
                AND o.date<=?
                AND (o.date>=? OR (t.recurrence='once' AND COALESCE(t.reminder_end_date,o.date)>=?))
                ORDER BY o.date, o.time""", (today, yesterday, yesterday),
            ).fetchall()
            for row in rows:
                grace = grace_due(row, now)
                if not grace and (not row["reminder"] or not row["time"] or not notification_due(row, now)):
                    continue
                title = row["title"]
                body = "Время сделать шаг вперёд. Открой Ритм и отметь задачу."
                if grace:
                    body = "Срок продлён на два часа. Ещё можно выполнить дело или зачеркнуть его после окончания продления."
                cursor = connection.execute(
                    "INSERT INTO notifications "
                    "(occurrence_id, title, body, created_at) VALUES (?,?,?,?)",
                    (row["id"], title, body, now.isoformat()),
                )
                column = "grace_notified_at" if grace else "notified_at"
                connection.execute(
                    f"UPDATE occurrences SET {column}=?,snooze_until=NULL WHERE id=?",
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
            all_rows = connection.execute("SELECT o.*,t.recurrence,t.start_date,t.reminder_end_date FROM occurrences o JOIN tasks t ON t.id=o.task_id").fetchall()
            notifications = connection.execute(
                "SELECT n.*, CASE WHEN t.archived_date IS NOT NULL "
                "AND o.status='pending' THEN 'cancelled' WHEN o.dismissed_at IS NOT NULL THEN 'dismissed' ELSE o.status "
                "END AS task_status FROM notifications n "
                "LEFT JOIN occurrences o ON o.id=n.occurrence_id "
                "LEFT JOIN tasks t ON t.id=o.task_id "
                "ORDER BY n.id DESC LIMIT 100"
            ).fetchall()
            seen = connection.execute(
                "SELECT value FROM settings WHERE key='dictor_last_seen'"
            ).fetchone()
        events = completion_events(all_rows)
        history = events[:100]
        by_day, completed_days, missed_days = {}, {}, {}
        for row in all_rows:
            data = decorate_checks(row,self.clock())
            if row['status']=='cancelled' and not data['checks']:
                continue
            count = len(data['slots']) if data['slots'] else 1
            done = len(data['checks']) if data['slots'] else int(row['status']=='done')
            group = by_day.setdefault(row['date'],dict(date=row['date'],total=0,done=0))
            group['total'] += count
            group['done'] += done
            missed = sum(item['status']=='missed' for item in data['checkins']) if data['slots'] else int(missed_deadline(row))
            if row['status']=='cancelled': missed=0
            missed_days[row['date']] = missed_days.get(row['date'],0) + missed
        for event in events:
            date = event['completed_at'][:10]
            completed_days[date] = completed_days.get(date,0) + 1
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
        total_done = len(events)
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
            "tasks": [dict(occurrence_state(row, self.clock()), reminder=bool(row["reminder"])) for row in rows] + (
                [dict(occurrence_state(row, self.clock()), reminder=bool(row["reminder"])) for row in unfinished
                 if occurrence_state(row, self.clock())["in_grace"]] if day == today else []),
            "unfinished": [dict(occurrence_state(row, self.clock()), reminder=bool(row["reminder"])) for row in unfinished],
            "week": week, "activity": activity,
            "summary": {"done": total_done, "xp": total_done * 10,
                        "level": total_done // 10 + 1,
                        "active_days": len(done_dates), "streak": streak,
                        "longest_streak": longest},
            "history": [{key:row.get(key) for key in ('id','task_id','date','title','category','completed_at','slot_time')} for row in history],
            "progress_events": [{key:row.get(key) for key in ('id','task_id','date','title','completed_at','slot_time')} for row in events],
            "progress_facts": {"perfect_days":sum(item['total']>0 and item['done']==item['total'] for date,item in by_day.items() if date<=today.isoformat())},
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
            all_rows = connection.execute("SELECT o.*,t.recurrence,t.start_date,t.reminder_end_date FROM occurrences o JOIN tasks t ON t.id=o.task_id").fetchall()
        done = [event for event in completion_events(all_rows) if event['completed_at'][:10]==selected]
        unfinished = []
        for row in all_rows:
            if row['date'] != selected or row['status']=='cancelled':
                continue
            data = decorate_checks(row,self.clock())
            if data['slots']:
                unfinished.extend(dict(data,title=data['title']+' · '+item['time'],slot_time=item['time']) for item in data['checkins'] if item['status']!='done')
            elif missed_deadline(row):
                unfinished.append(dict(row))
        return {"date":selected,"is_past":day<today,"done":done,"unfinished":unfinished}

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
                "SELECT id,date,title,category,time,status,dismissed_at FROM occurrences "
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
