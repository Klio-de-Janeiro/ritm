"""Проверка настоящего процесса, блокировки и перезапуска SQLite."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from integrations.dictor_client import RitmClient
from ritm.platform import InstanceLock


class RuntimeTests(unittest.TestCase):
    """Запустить приложение как отдельную программу без GUI."""

    def test_process_restart_reuses_database_and_excludes_second_instance(self):
        """Запуск, команда Dictor, второй экземпляр и перезапуск сохраняют данные."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            entry = Path(__file__).resolve().parents[1] / "main.py"
            arguments = [
                sys.executable, str(entry), "--background", "--no-tray",
                "--port", "0", "--data-dir", str(directory),
            ]
            process = subprocess.Popen(arguments)
            try:
                client = self.wait_for_process(directory, process)
                first_runtime = self.read_runtime(directory)
                client.create_task("Сохранить прогресс", request_id="runtime-1")
                occurrence = client.state()["tasks"][0]
                client.complete_task(occurrence["id"])
                second = subprocess.run(arguments, timeout=5, check=False)
                self.assertEqual(second.returncode, 0)
                runtime = json.loads((directory / "runtime.json").read_text())
                self.assertEqual(runtime["pid"], first_runtime["pid"])
                self.assertEqual(runtime["session"], first_runtime["session"])
                client._request("/app/quit", {})
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                self.stop_process(process, directory)
            process = subprocess.Popen(arguments)
            try:
                client = self.wait_for_process(
                    directory, process, first_runtime["session"]
                )
                current = client.state()
                self.assertEqual(current["summary"]["done"], 1)
                self.assertEqual(current["history"][0]["title"], "Сохранить прогресс")
                client._request("/app/quit", {})
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                self.stop_process(process, directory)

    def read_runtime(self, directory):
        """Прочитать runtime проверочного, а не пользовательского профиля."""
        return json.loads((directory / "runtime.json").read_text("utf-8"))

    def test_launcher_and_backend_can_have_different_pids(self):
        """Сценарий Windows-redirector воспроизводится отдельным родителем."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            entry = Path(__file__).resolve().parents[1] / "main.py"
            wrapper = (
                "import subprocess,sys; "
                "child=subprocess.Popen(sys.argv[1:]); "
                "raise SystemExit(child.wait())"
            )
            process = subprocess.Popen([
                sys.executable, "-c", wrapper, sys.executable, str(entry),
                "--background", "--no-tray", "--port", "0",
                "--data-dir", str(directory),
            ])
            try:
                client = self.wait_for_process(directory, process)
                self.assertNotEqual(self.read_runtime(directory)["pid"],
                                    process.pid)
                client._request("/app/quit", {})
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                self.stop_process(process, directory)

    def wait_for_process(self, directory, process, previous_session=None):
        """Дождаться новой сессии API независимо от PID Windows-redirector."""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() not in (None, 0):
                self.fail("Процесс Ритма завершился при запуске.")
            try:
                runtime = self.read_runtime(directory)
                if runtime["session"] != previous_session:
                    client = RitmClient.from_local_profile(directory)
                    client.state()
                    return client
            except (OSError, ValueError, KeyError, RuntimeError):
                pass
            time.sleep(0.03)
        log = directory / "ritm.log"
        detail = log.read_text("utf-8") if log.exists() else "Журнал не создан."
        self.fail("Локальный API не запустился за 20 секунд.\n" + detail)

    def stop_process(self, process, directory):
        """Закрыть собственный API и дождаться освобождения временных файлов."""
        try:
            client = RitmClient.from_local_profile(directory)
            client._request("/app/quit", {})
        except (OSError, RuntimeError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            lock = InstanceLock(directory)
            try:
                if lock.acquire():
                    return
            finally:
                lock.close()
            time.sleep(0.05)
        self.fail("Проверочный процесс не освободил временный профиль.")


if __name__ == "__main__":
    unittest.main()
