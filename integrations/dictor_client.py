"""Клиент Ритма для Dictor. Использует только стандартную библиотеку."""

import json
import os
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class RitmClient:
    """Передавать структурные намерения Dictor в локальное приложение."""

    def __init__(self, base_url, token, timeout=8):
        """Принять только локальный HTTP-адрес и приватный bearer-токен."""
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("127.0.0.1", "localhost")
            or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        ):
            raise ValueError("Разрешён только локальный HTTP origin Ритма.")
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("Необходим токен из настроек Ритма.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))

    @classmethod
    def from_local_profile(cls, directory=None):
        """Прочитать адрес работающего Ритма из профиля того же пользователя."""
        if directory is None:
            override = os.environ.get("RITM_DATA_DIR")
            if override:
                directory = Path(override)
            elif os.name == "nt":
                directory = Path(os.environ["LOCALAPPDATA"]) / "Ritm"
            else:
                base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")
                directory = Path(base) / "ritm"
        directory = Path(directory)
        try:
            runtime = json.loads((directory / "runtime.json").read_text("utf-8"))
            port = runtime["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Некорректный локальный порт Ритма.")
            token = (directory / "api.key").read_text("utf-8").strip()
        except (OSError, KeyError, ValueError) as error:
            raise RuntimeError("Сначала запусти приложение Ритм.") from error
        return cls(f"http://127.0.0.1:{port}", token)

    def _request(self, path, payload=None):
        """Выполнить запрос без proxy; ошибки не содержат токена доступа."""
        data = None if payload is None else json.dumps(
            payload, ensure_ascii=False
        ).encode("utf-8")
        request = Request(
            self.base_url + "/api/v1" + path, data=data,
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                message = json.load(error).get("error", "Ошибка API Ритма.")
            except (ValueError, AttributeError):
                message = "Ошибка API Ритма."
            raise RuntimeError(f"Ритм HTTP {error.code}: {message}") from None
        except (URLError, TimeoutError) as error:
            raise RuntimeError(
                "Ритм недоступен. Проверь, что приложение запущено."
            ) from error

    def state(self, day=None):
        """Получить задачи дня и occurrence_id для явного выполнения."""
        if day is not None:
            day = date.fromisoformat(day).isoformat()
        return self._request("/state" + ("?date=" + day if day else ""))

    def execute(self, command):
        """Отправить проверенный структурный результат маршрутизатора Dictor."""
        return self._request("/commands", command)

    def create_task(self, title, *, request_id, start_date=None,
                    recurrence="once", time=None, reminder=False,
                    category="Личное", priority="normal", notes="",
                    reminder_end_date=None, event_date=None, escalation_days=3):
        """Создать задачу; request_id должен быть постоянным для одной команды."""
        task = {
            "title": title, "recurrence": recurrence, "time": time,
            "reminder": reminder, "category": category,
            "priority": priority, "notes": notes,
            "reminder_end_date": reminder_end_date, "event_date": event_date,
            "escalation_days": escalation_days,
        }
        if reminder and time is None:
            task["time"] = "09:00"
        if start_date:
            task["start_date"] = start_date
        return self.execute({"action": "task.create", "request_id": request_id,
                             "task": task})

    def complete_task(self, occurrence_id, completed=True):
        """Установить статус конкретной задачи по точному идентификатору."""
        return self.execute({"action": "task.complete",
                             "occurrence_id": occurrence_id,
                             "completed": completed})
