"""Поведенческие проверки расписаний и сохранности истории."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ritm.store import Store, ValidationError


class StoreTests(unittest.TestCase):
    """Проверять основные сценарии на изолированной SQLite-базе."""

    def setUp(self):
        """Создать независимый профиль и управляемые часы."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = datetime(2026, 10, 15, 9, 0, tzinfo=timezone(timedelta(hours=3)))
        self.path = Path(self.directory.name) / "test.sqlite3"
        self.store = Store(self.path, clock=lambda: self.now)

    def state(self, day=None):
        """Прочитать нужный день с учётом тестовых часов."""
        return self.store.state(day or self.now.date().isoformat())

    def create(self, **fields):
        """Создать задачу с коротким тестовым названием."""
        return self.store.create_task(dict(title="Тестовая задача", **fields))

    def test_daily_rollover_keeps_completed_snapshot(self):
        """Новый день не сбрасывает завершённую запись предыдущего."""
        task = self.create(recurrence="daily")
        yesterday = self.state()["tasks"][0]
        self.store.complete(yesterday["id"])
        self.now += timedelta(days=1)
        today = self.state()["tasks"][0]
        self.assertNotEqual(today["id"], yesterday["id"])
        self.assertEqual(today["status"], "pending")
        self.store.update_task(task["id"], {"title": "Новое название"})
        old = self.state("2026-10-15")["tasks"][0]
        self.assertEqual(old["title"], "Тестовая задача")
        self.assertEqual(old["status"], "done")
        self.assertEqual(self.state()["tasks"][0]["title"], "Новое название")

    def test_restart_backfills_missing_daily_occurrences(self):
        """Простой приложения не стирает пропущенные дни расписания."""
        self.create(recurrence="daily")
        self.now += timedelta(days=5)
        restarted = Store(self.path, clock=lambda: self.now)
        for day in ("2026-10-16", "2026-10-17", "2026-10-19"):
            self.assertEqual(len(restarted.state(day)["tasks"]), 1)
        self.assertEqual(restarted.state("2026-10-20")["summary"]["done"], 0)

    def test_weekdays_and_weekly_schedule(self):
        """Будни и недельный интервал соблюдают дату начала."""
        self.create(recurrence="weekdays")
        self.create(recurrence="weekly")
        self.assertEqual(len(self.state("2026-10-16")["tasks"]), 1)
        self.assertEqual(len(self.state("2026-10-17")["tasks"]), 0)
        self.assertEqual(len(self.state("2026-10-22")["tasks"]), 2)

    def test_creation_does_not_invent_past_tasks(self):
        """Новая привычка не возникает в истории до даты создания."""
        self.create(recurrence="daily")
        self.assertEqual(self.state("2026-10-14")["tasks"], [])
        with self.assertRaises(ValidationError):
            self.create(start_date="2026-10-14")

    def test_overdue_once_moves_to_unfinished_without_more_notifications(self):
        """После конца дня задача сохраняется в списке невыполненных."""
        task = self.create(time="10:00", reminder=True)
        self.now += timedelta(days=2)
        self.assertEqual(self.state()["tasks"], [])
        self.assertEqual(len(self.state()["unfinished"]), 1)
        self.store.archive_task(task["id"])
        self.assertEqual(self.state()["tasks"], [])
        self.assertEqual(self.store.due_notifications(), [])

    def test_repeated_completion_does_not_add_xp(self):
        """Повторный запрос выполнения не меняет время и число XP."""
        self.create()
        occurrence = self.state()["tasks"][0]
        self.store.complete(occurrence["id"])
        completed_at = self.state()["history"][0]["completed_at"]
        self.now += timedelta(minutes=20)
        self.store.complete(occurrence["id"])
        current = self.state()
        self.assertEqual(current["summary"]["xp"], 10)
        self.assertEqual(current["history"][0]["completed_at"], completed_at)
        self.store.complete(occurrence["id"], False)
        self.assertEqual(self.state()["summary"]["xp"], 0)

    def test_future_completion_is_rejected(self):
        """Нельзя набрать прогресс на будущих задачах."""
        self.create(start_date="2026-10-16")
        future = self.state("2026-10-16")["tasks"][0]
        with self.assertRaises(ValidationError):
            self.store.complete(future["id"])

    def test_parallel_dictor_retries_create_one_task(self):
        """Одновременные повторы команды защищены одной транзакцией."""
        payload = {"title": "Команда Dictor", "recurrence": "daily"}
        with ThreadPoolExecutor(max_workers=6) as executor:
            tasks = list(executor.map(
                lambda _: self.store.create_task(payload, "stable-id"),
                range(12),
            ))
        self.assertEqual(len({task["id"] for task in tasks}), 1)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_idempotency_key_conflict_is_explicit(self):
        """Одинаковый ID с другим содержимым не скрывает ошибку Dictor."""
        self.store.create_task({"title": "Первое"}, "same")
        with self.assertRaises(ValidationError) as caught:
            self.store.create_task({"title": "Другое"}, "same")
        self.assertEqual(caught.exception.status, 409)

    def test_daily_notification_is_durable_and_snoozable(self):
        """Доставка фиксируется в базе, snooze создаёт новое событие позже."""
        self.create(time="10:00", reminder=True, recurrence="daily")
        self.assertEqual(self.store.due_notifications(), [])
        self.now += timedelta(hours=1)
        items = self.store.due_notifications()
        self.assertEqual(len(items), 1)
        self.assertEqual(self.store.due_notifications(), [])
        restarted = Store(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.due_notifications(), [])
        occurrence_id = self.state()["tasks"][0]["id"]
        self.store.snooze(occurrence_id, 10)
        self.now += timedelta(minutes=9)
        self.assertEqual(self.store.due_notifications(), [])
        self.now += timedelta(minutes=1)
        self.assertEqual(len(self.store.due_notifications()), 1)
        self.store.complete(occurrence_id)
        self.assertEqual(self.store.due_notifications(), [])

    def test_disabled_global_reminders_are_not_consumed(self):
        """Выключатель не теряет ещё не доставленное напоминание."""
        self.create(time="08:00", reminder=True)
        self.store.update_settings({"notifications": False})
        self.assertEqual(self.store.due_notifications(), [])
        self.store.update_settings({"notifications": True})
        self.assertEqual(len(self.store.due_notifications()), 1)

    def test_old_recurring_reminders_do_not_flood_after_restart(self):
        """При запуске проверяется сегодняшний день, а не все прошлые."""
        self.create(time="08:00", reminder=True, recurrence="daily")
        self.now += timedelta(days=5)
        self.assertEqual(len(self.store.due_notifications()), 1)
        self.assertEqual(self.store.due_notifications(), [])

    def test_archive_preserves_done_and_undo_does_not_resurrect(self):
        """Архив не удаляет победу и не возвращает задачу при отмене отметки."""
        task = self.create(recurrence="daily", reminder=True, time="08:00")
        occurrence = self.state()["tasks"][0]
        self.store.complete(occurrence["id"])
        self.store.archive_task(task["id"])
        self.assertEqual(self.state()["summary"]["done"], 1)
        self.assertEqual(self.state("2026-10-16")["tasks"], [])
        self.store.complete(occurrence["id"], False)
        self.assertEqual(self.state()["tasks"], [])
        self.assertEqual(self.state()["summary"]["done"], 0)
        self.assertEqual(self.store.due_notifications(), [])

    def test_edit_updates_future_pending_but_not_done(self):
        """Редактирование не оставляет устаревшие будущие записи."""
        task = self.create(recurrence="daily")
        first = self.state()["tasks"][0]
        self.store.complete(first["id"])
        self.state("2026-10-17")
        self.store.update_task(task["id"], {
            "title": "Только в будни", "recurrence": "weekdays"
        })
        self.assertEqual(self.state()["tasks"][0]["title"], "Тестовая задача")
        self.assertEqual(self.state("2026-10-17")["tasks"], [])
        self.assertEqual(self.state("2026-10-16")["tasks"][0]["title"], "Только в будни")

    def test_streak_uses_actual_completion_days(self):
        """Отметка старой задачи считается сегодняшней активностью."""
        self.create(recurrence="daily")
        old = self.state()["tasks"][0]
        self.now += timedelta(days=3)
        self.store.complete(old["id"])
        current = self.state()
        self.assertEqual(current["summary"]["streak"], 1)
        self.assertEqual(current["activity"][-1]["done"], 1)
        self.assertEqual(current["activity"][-4]["done"], 0)

    def test_overdue_once_edit_preserves_past_plan_and_stops_reminder(self):
        """Редактирование шаблона не переписывает план прошлого дня."""
        task = self.create(time="08:00", reminder=True)
        occurrence = self.state()["tasks"][0]
        self.store.due_notifications()
        self.now += timedelta(days=2)
        self.store.update_task(task["id"], {
            "title": "Исправлено", "notes": "Новая заметка",
            "time": "10:00", "category": "Работа", "priority": "high",
        })
        updated = self.state()["unfinished"][0]
        self.assertEqual(updated["id"], occurrence["id"])
        self.assertEqual(updated["title"], "Тестовая задача")
        self.assertEqual(updated["time"], "08:00")
        self.assertEqual(self.store.due_notifications(), [])

    def test_overdue_once_reschedule_does_not_leave_duplicate(self):
        """Перенос просроченной разовой отменяет прежнее ожидание."""
        task = self.create(time="08:00", reminder=True)
        self.now += timedelta(days=2)
        self.store.update_task(task["id"], {"start_date": "2026-10-18"})
        self.assertEqual(self.state()["tasks"], [])
        self.assertEqual(self.store.due_notifications(), [])
        future = self.state("2026-10-18")["tasks"]
        self.assertEqual(len(future), 1)
        self.assertEqual(future[0]["task_id"], task["id"])

    def test_future_task_can_move_earlier_without_duplicates(self):
        """Будущую задачу можно перенести на сегодня или ближайший день."""
        task = self.create(start_date="2026-10-20")
        self.store.update_task(task["id"], {"start_date": "2026-10-15"})
        self.assertEqual(len(self.state()["tasks"]), 1)
        self.assertEqual(self.state("2026-10-20")["tasks"], [])

    def test_streak_waits_for_today_until_day_ends(self):
        """Вчерашняя серия ещё действует утром следующего дня."""
        self.create(recurrence="daily")
        self.store.complete(self.state()["tasks"][0]["id"])
        self.now += timedelta(days=1)
        self.assertEqual(self.state()["summary"]["streak"], 1)
        self.store.complete(self.state()["tasks"][0]["id"])
        self.assertEqual(self.state()["summary"]["longest_streak"], 2)
        self.now += timedelta(days=2)
        self.assertEqual(self.state()["summary"]["streak"], 0)

    def test_validation_rejects_invalid_structures(self):
        """Неявные bool, неизвестные поля и невозможные даты запрещены."""
        invalid = [
            {"title": " "}, {"title": "x", "reminder": "false"},
            {"title": "x", "time": "25:00"},
            {"title": "x", "time": None, "reminder": True},
            {"title": "x", "category": "Другая"},
            {"title": "x", "start_date": "2026-02-30"},
            {"title": "x", "shell": "whoami"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                self.store.create_task(payload)
        self.assertEqual(self.store.list_tasks(), [])

    def test_calendar_contains_six_weeks_and_matching_events(self):
        """Месяц содержит 42 дня с реальными экземплярами расписаний."""
        self.create(recurrence="daily")
        calendar = self.store.calendar("2026-10")
        self.assertEqual(len(calendar["days"]), 42)
        day = next(item for item in calendar["days"] if item["date"] == "2026-10-16")
        self.assertEqual(len(day["tasks"]), 1)

    def test_export_omits_private_runtime_and_request_keys(self):
        """Переносимый экспорт содержит историю, но не токены/идемпотентность."""
        self.store.create_task({"title": "Пример"}, "private-request-id")
        exported = self.store.export()
        self.assertEqual(len(exported["tasks"]), 1)
        self.assertNotIn("requests", exported)
        self.assertNotIn("autostart", exported["settings"])
        self.assertNotIn("token", exported)

    def test_newer_schema_cannot_be_downgraded(self):
        """Старая сборка не должна переписывать схему новой базы."""
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA user_version=5")
            connection.commit()
        with self.assertRaises(RuntimeError):
            Store(self.path)


if __name__ == "__main__":
    unittest.main()
