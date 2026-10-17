"""Ограниченный доступ ChatGPT к задачам через существующий API Ритма."""

import uuid

from integrations.dictor_client import RitmClient
from ritm.config import CATEGORIES, PRIORITIES, RECURRENCES


class GptTools:
    """Читать день и создавать задачи, не раскрывая токены и настройки."""

    def __init__(self, directory=None):
        """Выбрать профиль того же пользователя Windows, что запустил Ритм."""
        self.directory = directory

    def _client(self):
        """Заново находить локальный порт после перезапуска приложения."""
        return RitmClient.from_local_profile(self.directory)

    def context(self):
        """Получить дату и время ноутбука для слов «завтра» и «вечером»."""
        state = self._client().state()
        return {
            "today": state["today"], "server_time": state["server_time"],
            "categories": list(CATEGORIES), "priorities": list(PRIORITIES),
            "recurrences": list(RECURRENCES),
            "default_reminder_time": "09:00",
            "three_times_daily": ["09:00", "15:00", "21:00"],
            "reminders_enabled": state["settings"]["notifications"],
        }

    def list_day(self, day=None):
        """Вернуть задачи выбранного дня без заметок, истории и секретов."""
        state = self._client().state(day)
        fields = ("task_id", "date", "title", "time", "reminder",
                  "category", "priority", "recurrence", "status")
        return {
            "date": state["selected_date"],
            "tasks": [{key: row[key] for key in fields}
                      for row in state["tasks"]],
        }

    def create_task(self, request_id, title, start_date, *, time=None,
                    reminder=False, recurrence="once", category="Личное",
                    priority="normal", notes="", reminder_end_date=None,
                    event_date=None, escalation_days=3):
        """Записать задачу; повтор одной команды сохраняет тот же UUID."""
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("request_id должен быть UUID в нижнем регистре.") from error
        result = self._client().create_task(
            title, request_id="gpt-" + request_id, start_date=start_date,
            time=time, reminder=reminder, recurrence=recurrence,
            category=category, priority=priority, notes=notes,
            reminder_end_date=reminder_end_date, event_date=event_date,
            escalation_days=escalation_days,
        )
        task = result["task"]
        fields = ("id", "title", "start_date", "time", "reminder",
                  "recurrence", "category", "priority", "reminder_end_date",
                  "event_date", "escalation_days")
        return {"saved": True, "task": {key: task[key] for key in fields}}
