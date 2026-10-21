"""Several companions share one database, without replacing one another's pairing."""
import http.client
import json
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from ritm.mobile_sync import SyncEngine, SyncHandler, SyncService
from ritm.store import Store, ValidationError


class DevicesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = Store(self.root / "data.sqlite3", clock=lambda: datetime(2026, 10, 21, 12, tzinfo=timezone.utc))
        self.service = SyncService(self.store, self.root)
        self.addCleanup(self.service.close)
        self.engine = self.service.engine
        self.a = {"id": str(uuid.uuid4()), "name": "Phone <A>"}
        self.b = {"id": str(uuid.uuid4()), "name": "Tablet B"}

    def body(self, device, operations=()):
        return dict(protocol=2, server_id=self.engine.server_id, device=device, operations=list(operations))

    def test_device_identity_survives_transport_change_and_server_restart(self):
        token = self.service.config["token"]
        self.engine.exchange(self.body(self.a), transport="USB")
        self.engine.exchange(self.body(self.b), transport="LAN")
        self.engine.exchange(self.body(self.a), transport="LAN")
        status = self.service.status()
        self.assertEqual(len(status["devices"]), 2)
        self.assertTrue(all(row["transport"] == "LAN" for row in status["devices"]))
        self.assertEqual(self.service.config["token"], token)
        restarted = SyncService(self.store, self.root)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.status()["devices"], status["devices"])
        self.assertEqual(restarted.config["token"], token)

    def test_concurrent_http_clients_union_changes_and_replay_is_idempotent(self):
        laptop = self.store.create_task(dict(title="Laptop"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), SyncHandler)
        server.engine, server.token = self.engine, "x" * 43
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def post(body, token=server.token):
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                client.request("POST", "/sync/v1", json.dumps(body), {"Content-Type": "application/json", "Authorization": "Bearer " + token})
                response = client.getresponse()
                return response.status, json.loads(response.read())
            finally:
                client.close()
        try:
            bodies = [self.body(device, [dict(id=str(uuid.uuid4()), task_id=str(uuid.uuid4()), kind="create",
                        values=dict(title=device["name"], start_date="2026-10-21"))]) for device in (self.a, self.b)]
            self.assertEqual(post(bodies[0], "bad")[0], 401)
            self.assertEqual(self.service.status()["devices"], [])
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(post, bodies))
            self.assertEqual([status for status, _ in results], [200, 200])
            expected = {laptop["id"]} | {body["operations"][0]["task_id"] for body in bodies}
            for body in bodies:
                status, response = post(body)
                self.assertEqual({task["id"] for task in response["tasks"]}, expected)
                self.assertEqual(response["conflicts"], [])
            self.assertEqual(len(self.service.status()["devices"]), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_metadata_is_validated_and_legacy_clients_still_sync(self):
        for device in ({"id": "bad", "name": "Phone"}, {"id": self.a["id"], "name": "x" * 81}, {"id": self.a["id"], "name": "\n"}):
            with self.assertRaises(ValidationError):
                self.engine.exchange(self.body(device))
        self.assertEqual(self.service.status()["devices"], [])
        self.assertEqual(self.engine.exchange(dict(protocol=2))["conflicts"], [])
