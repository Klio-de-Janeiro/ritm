"""Проверки офлайн-команд, конфликтов, квитанций и отдельного LAN API."""

import http.client
import json
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ritm.mobile_sync import SyncEngine, SyncService, SyncHandler, FIELDS
from ritm.store import Store, ValidationError
from ritm.assistant_plan import import_plan, preview_plan
from ritm.platform import open_window
from http.server import ThreadingHTTPServer


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        self.directory = Path(self.temp.name)
        self.store = Store(self.directory / "data.sqlite3", clock=lambda: self.now)
        self.server_id = str(uuid.uuid4())
        self.engine = SyncEngine(self.store, self.server_id)

    def exchange(self, operations=()):
        return self.engine.exchange({"protocol": 2, "server_id": self.server_id,
                                     "operations": list(operations)})

    def operation(self, kind, task_id, **fields):
        return dict(id=str(uuid.uuid4()), kind=kind, task_id=task_id, **fields)

    def create(self, day="2026-10-16"):
        op = self.operation("create", str(uuid.uuid4()), values={
            "title": "Офлайн шаг", "start_date": day, "recurrence": "daily"})
        result = self.exchange([op])
        self.assertEqual(result["accepted"], [op["id"]])
        return op, result["tasks"][0]

    def test_offline_creation_and_completion_replay_survive_restart(self):
        create, task = self.create("2026-10-15")
        complete = self.operation("complete", task["id"], date="2026-10-15", completed=True,
            completed_at="2026-10-15T18:00:00+03:00", before={"status": "pending", "completed_at": None})
        result = self.exchange([complete])
        self.assertEqual(result["accepted"], [complete["id"]])
        self.engine = SyncEngine(Store(self.store.path, clock=lambda: self.now), self.server_id)
        replay = self.exchange([create, complete])
        self.assertEqual(len(replay["tasks"]), 1)
        self.assertEqual(sum(o["status"] == "done" for o in replay["occurrences"]), 1)
        self.assertTrue(any(o["date"] == "2026-10-16" and o["status"] == "pending" for o in replay["occurrences"]))

    def test_two_devices_can_merge_different_fields_but_not_overwrite_title(self):
        _, task = self.create()
        self.store.update_task(task["id"], {"title": "Правка ноутбука"})
        notes = self.operation("edit", task["id"], values={"notes": "С телефона"}, before={"notes": ""})
        title = self.operation("edit", task["id"], values={"title": "Правка телефона"}, before={"title": task["title"]})
        result = self.exchange([notes, title])
        self.assertEqual(result["accepted"], [notes["id"]])
        self.assertEqual(result["conflicts"][0]["id"], title["id"])
        self.assertEqual(result["tasks"][0]["title"], "Правка ноутбука")
        self.assertEqual(result["tasks"][0]["notes"], "С телефона")

    def test_offline_plan_preserves_each_missed_day_before_later_edit(self):
        op = self.operation("create", str(uuid.uuid4()), values={
            "title": "Первоначальный план", "start_date": "2026-10-13", "recurrence": "daily"})
        edit = self.operation("edit", op["task_id"], values={"title": "Новый план"}, before={"title": "Первоначальный план"})
        result = self.exchange([op, edit])
        old = [row for row in result["occurrences"] if row["date"] < "2026-10-16"]
        self.assertEqual(len(old), 3)
        self.assertTrue(all(row["title"] == "Первоначальный план" for row in old))
        self.assertEqual(self.store.state("2026-10-16")["tasks"][0]["title"], "Новый план")

    def test_done_history_snapshot_survives_phone_edit(self):
        _, task = self.create()
        complete = self.operation("complete", task["id"], date="2026-10-16", completed=True,
            completed_at="2026-10-16T10:00:00+00:00", before={"status": "pending", "completed_at": None})
        edit = self.operation("edit", task["id"], values={"title": "Новый текст"}, before={"title": task["title"]})
        result = self.exchange([complete, edit])
        self.assertEqual(len(result["accepted"]), 2)
        self.assertEqual(result["occurrences"][0]["title"], "Офлайн шаг")
        self.assertEqual(result["tasks"][0]["title"], "Новый текст")

    def test_offline_complete_then_undo_in_one_batch(self):
        _, task = self.create()
        moment = "2026-10-16T10:00:00+00:00"
        complete = self.operation("complete", task["id"], date="2026-10-16", completed=True,
            completed_at=moment, before={"status": "pending", "completed_at": None})
        undo = self.operation("complete", task["id"], date="2026-10-16", completed=False,
            before={"status": "done", "completed_at": moment})
        result = self.exchange([complete, undo])
        self.assertEqual(result["accepted"], [complete["id"], undo["id"]])
        self.assertEqual(result["occurrences"][0]["status"], "pending")

    def test_stale_undo_conflicts_after_laptop_recompletion(self):
        _, task = self.create()
        row = self.store.state("2026-10-16")["tasks"][0]
        self.store.complete(row["id"])
        undo = self.operation("complete", task["id"], date="2026-10-16", completed=False,
            before={"status": "done", "completed_at": "2026-10-16T09:00:00+00:00"})
        result = self.exchange([undo])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["occurrences"][0]["status"], "done")

    def test_archive_keeps_completed_and_cancelled_cannot_be_completed(self):
        _, task = self.create()
        archive = self.operation("archive", task["id"], date="2026-10-16", before={k: task[k] for k in FIELDS})
        complete = self.operation("complete", task["id"], date="2026-10-16", completed=True,
            completed_at=self.now.isoformat(), before={"status": "pending", "completed_at": None})
        result = self.exchange([archive, complete])
        self.assertEqual(result["accepted"], [archive["id"]])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["occurrences"], [])

    def test_invalid_command_does_not_lose_independent_valid_command(self):
        _, task = self.create()
        invalid = self.operation("edit", task["id"], values={"title": ""}, before={"title": task["title"]})
        valid = self.operation("edit", task["id"], values={"notes": "Полезная правка"}, before={"notes": ""})
        result = self.exchange([invalid, valid])
        self.assertEqual(result["accepted"], [valid["id"]])
        self.assertEqual(result["tasks"][0]["title"], task["title"])
        self.assertEqual(result["tasks"][0]["notes"], "Полезная правка")

    def test_id_reuse_and_wrong_database_are_rejected(self):
        op, _ = self.create()
        op["values"]["title"] = "Другая команда"
        with self.assertRaises(ValidationError): self.exchange([op])
        with self.assertRaises(ValidationError): self.engine.exchange({"protocol": 2, "server_id": str(uuid.uuid4())})
        with self.assertRaises(ValidationError): self.engine.exchange({"protocol": 999})
        self.assertEqual(self.store.list_tasks()[0]["title"], "Офлайн шаг")

    def test_service_disabled_by_default_and_token_persistent(self):
        service = SyncService(self.store, self.directory)
        self.addCleanup(service.close)
        self.assertFalse(service.status()["enabled"])
        token = service.config["token"]
        second = SyncService(self.store, self.directory)
        self.addCleanup(second.close)
        self.assertEqual(second.config["token"], token)
        service.configure({"enabled": False, "rotate": True})
        self.assertNotEqual(service.config["token"], token)

    def test_http_rejects_browser_and_wrong_token_and_replays_authorized(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SyncHandler)
        server.engine, server.token = self.engine, "x" * 43
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for token, origin, expected in (("bad", None, 401), (server.token, "http://evil.invalid", 403), (server.token, None, 200)):
                client = http.client.HTTPConnection("127.0.0.1", server.server_port)
                headers = {"Content-Type": "application/json", "Authorization": "Bearer " + token}
                if origin: headers["Origin"] = origin
                client.request("POST", "/sync/v1", json.dumps({"protocol": 2, "operations": []}), headers)
                response = client.getresponse()
                self.assertEqual(response.status, expected)
                response.read(); client.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_assistant_preview_import_atomic_and_idempotent(self):
        body = {"tasks": [{"title": "Шаг 1"}, {"title": "Шаг 2"}], "request_id": "plan-test"}
        self.assertEqual(len(preview_plan(self.store, body)), 2)
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(import_plan(self.store, body)["created"], 2)
        import_plan(self.store, body)
        self.assertEqual(len(self.store.list_tasks()), 2)
        bad = {"tasks": [{"title": "Не записывать"}, {"title": ""}], "request_id": "plan-invalid"}
        with self.assertRaises(ValidationError): import_plan(self.store, bad)
        self.assertEqual(len(self.store.list_tasks()), 2)

    def test_chromium_launch_requests_fullscreen_with_private_profile(self):
        with patch("ritm.platform.browser_executable", return_value="msedge.exe"), patch("ritm.platform.subprocess.Popen") as process:
            open_window("http://127.0.0.1:8768", self.directory)
            args = process.call_args.args[0]
            self.assertIn("--start-fullscreen", args)
            self.assertIn("--user-data-dir=" + str(self.directory / "browser-profile"), args)
