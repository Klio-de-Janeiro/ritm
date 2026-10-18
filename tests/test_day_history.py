"""Конец дня, пропущенные планы и фактические даты выполнения."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ritm.store import Store


class DayHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 16, 20, 59, tzinfo=timezone(timedelta(hours=3)))
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.store = Store(self.path, clock=lambda: self.now)

    def test_lecture_notifies_once_at_21_and_expires_at_midnight(self):
        task = self.store.create_task({"title": "Лекция по физике", "time": "21:00", "reminder": True})
        self.assertEqual(task["recurrence"], "once")
        self.assertEqual(task["reminder_end_date"], "2026-10-16")
        self.assertEqual(self.store.due_notifications(), [])
        self.now = self.now.replace(hour=21, minute=0)
        self.assertEqual(len(self.store.due_notifications()), 1)
        self.assertEqual(self.store.due_notifications(), [])
        self.now = self.now.replace(hour=23, minute=59)
        self.store.snooze(self.store.state("2026-10-16")["tasks"][0]["id"], 10)
        self.now = self.now.replace(day=17, hour=0, minute=9)
        self.assertEqual(self.store.due_notifications(), [])
        state = self.store.state("2026-10-17")
        self.assertTrue(state["tasks"][0]["in_grace"])
        self.assertEqual(state["tasks"][0]["grace_until"], "2026-10-17T02:00:00+03:00")
        self.assertEqual(state["unfinished"], [])
        self.assertEqual(self.store.day_details("2026-10-16")["unfinished"][0]["title"], "Лекция по физике")

    def test_restart_after_missed_day_does_not_deliver_old_reminder(self):
        self.store.create_task({"title": "Пропущено", "time": "21:00", "reminder": True})
        self.now += timedelta(days=3)
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.due_notifications(), [])
        self.assertEqual(len(self.store.state("2026-10-19")["unfinished"]), 1)

    def test_late_completion_is_recorded_on_actual_day_and_keeps_original_miss(self):
        self.store.create_task({"title": "Лекция"})
        row = self.store.state("2026-10-16")["tasks"][0]
        self.now += timedelta(days=2)
        self.store.complete(row["id"])
        original = self.store.day_details("2026-10-16")
        actual = self.store.day_details("2026-10-18")
        self.assertEqual(original["done"], [])
        self.assertEqual(original["unfinished"][0]["completed_at"][:10], "2026-10-18")
        self.assertEqual(actual["done"][0]["title"], "Лекция")
        self.assertEqual(self.store.state("2026-10-18")["unfinished"], [])
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.day_details("2026-10-16"), original)
        self.assertEqual(self.store.state("2026-10-18")["activity"][-1]["done"], 1)
        self.assertEqual(self.store.state("2026-10-18")["activity"][-3]["unfinished"], 1)

    def test_reschedule_keeps_original_plan_without_old_notifications(self):
        task = self.store.create_task({"title": "Лекция", "time": "21:00", "reminder": True})
        self.now += timedelta(days=1)
        changed = self.store.update_task(task["id"], {"title": "Лекция завтра", "start_date": "2026-10-18"})
        self.assertEqual(changed["reminder_end_date"], "2026-10-18")
        self.assertEqual(self.store.due_notifications(), [])
        self.assertEqual(self.store.day_details("2026-10-16")["unfinished"][0]["title"], "Лекция")
        self.assertEqual(self.store.state("2026-10-18")["tasks"][0]["title"], "Лекция завтра")

    def test_explicit_recurrence_is_not_limited_to_the_first_day(self):
        task = self.store.create_task({"title": "Спорт", "time": "09:00", "reminder": True, "recurrence": "daily"})
        self.assertIsNone(task["reminder_end_date"])
        self.now += timedelta(days=1)
        self.assertEqual(len(self.store.due_notifications()), 1)
        self.assertEqual(len(self.store.state("2026-10-17")["unfinished"]), 1)

    def test_default_end_follows_a_change_from_once_to_daily(self):
        task = self.store.create_task({"title": "Спорт", "reminder": True})
        task = self.store.update_task(task["id"], {"recurrence": "daily"})
        self.assertIsNone(task["reminder_end_date"])
