"""Интервалы, переход суток и календарные месяцы без повторных сигналов."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ritm.config import HOUR_INTERVALS
from ritm.store import Store, ValidationError, scheduled


class IntervalTests(unittest.TestCase):
    """Управляемые часы позволяют проверять много дней без ожидания."""

    def setUp(self):
        """Открыть отдельную базу с московским временем."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 16, 9, tzinfo=timezone(timedelta(hours=3)))
        self.path = Path(self.temp.name) / "interval.sqlite3"
        self.store = Store(self.path, clock=lambda: self.now)

    def create(self, recurrence):
        """Задать начало отсчёта в 09:00 первого дня."""
        return self.store.create_task({"title": recurrence, "time": "09:00",
                                       "reminder": True, "recurrence": recurrence})

    def test_each_hour_interval_notifies_once_per_slot(self):
        """Все пять часовых вариантов доходят до своего следующего слота."""
        for recurrence, hours in HOUR_INTERVALS.items():
            task = self.create(recurrence)
            self.assertEqual(len(self.store.due_notifications()), 1)
            self.assertEqual(self.store.due_notifications(), [])
            self.now += timedelta(hours=hours, seconds=-1)
            self.assertEqual(self.store.due_notifications(), [])
            self.now += timedelta(seconds=1)
            self.assertEqual(len(self.store.due_notifications()), 1)
            self.store.archive_task(task["id"])
            self.now = datetime(2026, 10, 16, 9, tzinfo=self.now.tzinfo)

    def test_five_hour_interval_uses_a_daily_window(self):
        self.create("every_5_hours")
        self.store.due_notifications()
        self.now = self.now.replace(day=17,hour=0)
        self.assertEqual(self.store.due_notifications(),[])
        self.now = self.now.replace(hour=5)
        self.assertEqual(self.store.due_notifications(),[])
        self.now = self.now.replace(hour=9)
        self.assertEqual(len(self.store.due_notifications()),1)

    def test_restart_coalesces_missed_slots_and_done_stops_today(self):
        """После сна один актуальный сигнал; выполнение завершает день."""
        self.create("hourly")
        self.now += timedelta(hours=7)
        self.assertEqual(len(self.store.due_notifications()), 1)
        restarted = Store(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.due_notifications(), [])
        row = restarted.state(self.now.date().isoformat())["tasks"][0]
        restarted.complete(row["id"])
        self.now += timedelta(hours=1)
        self.assertEqual(len(restarted.due_notifications()), 1)
        self.now += timedelta(days=1)
        self.assertEqual(len(restarted.due_notifications()), 1)

    def test_hourly_snooze_is_consumed_and_schedule_continues(self):
        """Ручные 15 минут не превращаются в сигнал каждые 5 секунд."""
        self.create("hourly")
        row = self.store.state("2026-10-16")["tasks"][0]
        self.store.due_notifications()
        self.store.snooze(row["id"], 15)
        self.now += timedelta(minutes=15)
        self.assertEqual(len(self.store.due_notifications()), 1)
        self.assertEqual(self.store.due_notifications(), [])
        self.now += timedelta(minutes=45)
        self.assertEqual(len(self.store.due_notifications()), 1)

    def test_every_three_days_and_monthly_end_of_month(self):
        """Короткий месяц не переносит постоянную дату с 31 на 28."""
        task = {"start_date": "2026-01-31", "archived_date": None,
                "recurrence": "monthly"}
        from datetime import date
        for day, expected in (("2026-02-28", True), ("2026-03-28", False),
                              ("2026-03-31", True), ("2026-04-30", True)):
            self.assertEqual(scheduled(task, date.fromisoformat(day)), expected)
        task.update(start_date="2026-10-16", recurrence="every_3_days")
        for day, expected in (("2026-10-16", True), ("2026-10-17", False),
                              ("2026-10-19", True), ("2026-10-22", True)):
            self.assertEqual(scheduled(task, date.fromisoformat(day)), expected)

    def test_hourly_requires_enabled_reminder_and_time(self):
        """Неполный часовой будильник отклоняется до записи."""
        for fields in ({"reminder": False, "time": "09:00"},
                       {"reminder": True, "time": None}):
            with self.assertRaises(ValidationError):
                self.store.create_task(dict(title="Час", recurrence="hourly", **fields))
        self.assertEqual(self.store.list_tasks(), [])
