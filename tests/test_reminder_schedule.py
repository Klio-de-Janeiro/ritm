"""Окончание, фиксированные часы и учащение с управляемым временем."""

import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ritm.store import SCHEMA, Store, ValidationError
from ritm.mobile_sync import SyncEngine


class ReminderScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.now = datetime(2026, 10, 16, 8, 59, tzinfo=timezone(timedelta(hours=3)))
        self.store = Store(self.path, clock=lambda: self.now)

    def create(self, **values):
        return self.store.create_task(dict(title="Спорт", reminder=True, **values))

    def at(self, day, hour, minute=0):
        self.now = self.now.replace(day=day, hour=hour, minute=minute)
        return self.store.due_notifications()

    def test_default_time_and_three_daily_slots(self):
        task = self.create(recurrence="three_times_daily")
        self.assertEqual(task["time"], "09:00")
        self.assertEqual(self.store.due_notifications(), [])
        for hour in (9, 15, 21):
            self.assertEqual(len(self.at(16, hour)), 1)
            self.assertEqual(self.store.due_notifications(), [])
        self.assertEqual(self.at(17, 8, 59), [])
        self.assertEqual(len(self.at(17, 9)), 1)

    def test_missed_slots_restart_and_completion(self):
        self.create(recurrence="three_times_daily")
        self.assertEqual(len(self.at(16, 20)), 1)
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.due_notifications(), [])
        row = self.store.state("2026-10-16")["tasks"][0]
        self.store.complete(row["id"])
        self.assertEqual(self.at(16, 21), [])
        self.assertEqual(len(self.at(17, 9)), 1)

    def test_end_date_inclusive_even_for_snooze_and_overdue_once(self):
        self.create(recurrence="daily", reminder_end_date="2026-10-20")
        self.assertEqual(len(self.at(20, 9)), 1)
        row = self.store.state("2026-10-20")["tasks"][0]
        self.now = self.now.replace(hour=23, minute=30)
        self.store.snooze(row["id"], 60)
        self.assertEqual(self.at(21, 0, 30), [])
        self.assertEqual(self.at(21, 9), [])
        self.assertTrue(self.store.state("2026-10-21")["tasks"])
        self.create(reminder_end_date="2026-10-21")
        self.assertEqual(self.at(22, 9), [])

    def test_event_accelerates_at_boundary_and_finishes(self):
        self.create(recurrence="event", event_date="2026-10-20", escalation_days=3)
        self.assertEqual(len(self.at(16, 9)), 1)
        self.assertEqual(self.at(16, 15), [])
        for day in (17, 20):
            for hour in (9, 15, 21):
                self.assertEqual(len(self.at(day, hour)), 1)
        self.assertEqual(self.at(21, 9), [])
        self.assertEqual(self.store.state("2026-10-21")["tasks"], [])

    def test_edit_and_sync_preserve_options(self):
        task = self.create(recurrence="daily")
        self.store.update_task(task["id"], {"reminder_end_date": "2026-10-16"})
        self.assertEqual(self.at(17, 9), [])
        engine = SyncEngine(self.store, "test")
        op = {"id": str(uuid.uuid4()), "task_id": task["id"], "kind": "edit",
              "before": {"reminder_end_date": "2026-10-16", "recurrence": "daily", "event_date": None, "escalation_days": 3},
              "values": {"reminder_end_date": "2026-10-20", "recurrence": "event", "event_date": "2026-10-20", "escalation_days": 1}}
        result = engine.exchange({"protocol": 2, "operations": [op]})
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["tasks"][0]["escalation_days"], 1)
        self.assertEqual(len(self.store.due_notifications()), 1)
        self.assertEqual(self.at(17, 15), [])

    def test_invalid_dates_and_thresholds(self):
        for values in ({"reminder_end_date": "2026-10-15"}, {"reminder_end_date": "20.10.26"},
                       {"recurrence": "event"}, {"event_date": "2026-02-30"},
                       {"escalation_days": 0}, {"escalation_days": 366},
                       {"escalation_days": True}, {"escalation_days": 2.5}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.create(**values)

    def test_version_one_migrates_without_changing_tasks(self):
        old = Path(self.temp.name) / "old.sqlite3"
        with closing(sqlite3.connect(old)) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       ("old", "Спорт", "Заметка", "Здоровье", "normal", "daily",
                        "2026-10-16", "18:00", 1, self.now.isoformat(), None))
            db.execute("PRAGMA user_version=1")
            db.commit()
        migrated = Store(old, clock=lambda: self.now)
        task = migrated.list_tasks()[0]
        self.assertEqual(task["time"], "18:00")
        self.assertIsNone(task["reminder_end_date"])
        self.assertEqual(task["escalation_days"], 3)
        self.assertEqual(Store(old, clock=lambda: self.now).list_tasks(), [task])
