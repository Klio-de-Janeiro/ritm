import hashlib
import hmac
import json
import socket
import tempfile
import unittest
from pathlib import Path

from ritm.mobile_sync import SyncService
from ritm.store import Store


class DiscoveryTests(unittest.TestCase):
    def test_saved_pairing_proof_and_disabled_service(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = SyncService(Store(root / "tasks.sqlite3"), root)
            self.addCleanup(service.close)
            service.config["port"] = 0
            service.configure({"enabled": True})
            port = service.server.server_port
            nonce = "a" * 32
            request = dict(service="ritm-discover-v1", server_id=service.config["server_id"], nonce=nonce)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                client.sendto(json.dumps(request).encode(), ("127.0.0.1", port))
                response = json.loads(client.recv(2048))
                signed = f"{nonce}|{service.config['server_id']}|{port}".encode()
                self.assertEqual(response["proof"], hmac.new(service.config["token"].encode(), signed, hashlib.sha256).hexdigest())
                self.assertNotIn("token", response)
                request["server_id"] = "unknown-laptop"
                client.sendto(json.dumps(request).encode(), ("127.0.0.1", port))
                with self.assertRaises(socket.timeout):
                    client.recv(2048)
                service.configure({"enabled": False})
                self.assertIsNone(service.discovery)
                client.sendto(json.dumps(request).encode(), ("127.0.0.1", port))
                with self.assertRaises((socket.timeout, ConnectionResetError)):
                    client.recv(2048)
            restarted = SyncService(Store(root / "tasks.sqlite3"), root)
            self.addCleanup(restarted.close)
            self.assertEqual(restarted.config["token"], service.config["token"])
            self.assertEqual(restarted.config["server_id"], service.config["server_id"])
