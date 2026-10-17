"""Проверки записи из GPT через настоящий локальный API и MCP SDK."""

import asyncio
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from integrations.gpt_tools import GptTools
from ritm.config import HOUR_INTERVALS
from ritm.server import LocalServer
from ritm.store import Store
from setup_gpt import write_connection


class GptTests(unittest.TestCase):
    """Поднимать отдельный профиль, не затрагивая пользовательские задачи."""

    def setUp(self):
        """Связать клиент с сервером по тем же runtime-файлам, что в Windows."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = Store(self.directory / "ritm.sqlite3")
        self.token = "private-test-token-" + "z" * 32
        self.server = LocalServer(("127.0.0.1", 0), self.store,
                                  self.token, "session-" + "s" * 40, {
            "status": lambda: {}, "notify": lambda *_: None,
            "quit": lambda: None, "autostart": lambda _: None,
        })
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.shutdown)
        (self.directory / "runtime.json").write_text(json.dumps({
            "port": self.server.server_port, "session": "private-session",
        }), encoding="utf-8")
        (self.directory / "api.key").write_text(self.token, encoding="utf-8")
        self.tools = GptTools(self.directory)

    def shutdown(self):
        """Остановить обработчики до удаления временной базы."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_context_and_list_do_not_return_settings_notes_or_secrets(self):
        """Модель получает нужные даты, но не весь экспорт профиля."""
        self.store.create_task({"title": "Задача", "notes": "Личная заметка"})
        context = self.tools.context()
        self.assertEqual(context["today"], self.store.clock().date().isoformat())
        self.assertIn("server_time", context)
        day = self.tools.list_day()
        self.assertEqual(day["tasks"][0]["title"], "Задача")
        encoded = json.dumps([context, day], ensure_ascii=False)
        for secret in (self.token, "private-session", "Личная заметка"):
            self.assertNotIn(secret, encoded)
        self.assertNotIn("settings", context)

    def test_create_with_retry_writes_once(self):
        """Повтор инструмента возвращает ту же задачу и её параметры."""
        request_id = str(uuid.uuid4())
        day = self.store.clock().date().isoformat()
        first = self.tools.create_task(request_id, "Прогулка", day,
                                       time="19:00", reminder=True)
        second = self.tools.create_task(request_id, "Прогулка", day,
                                        time="19:00", reminder=True)
        self.assertEqual(first, second)
        self.assertTrue(first["saved"])
        self.assertTrue(first["task"]["reminder"])
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_reused_id_with_different_task_does_not_overwrite(self):
        """Конфликт запроса не превращается в ложный успех."""
        request_id, day = str(uuid.uuid4()), self.store.clock().date().isoformat()
        self.tools.create_task(request_id, "Первая", day)
        with self.assertRaises(RuntimeError):
            self.tools.create_task(request_id, "Другая", day)
        self.assertEqual(self.store.list_tasks()[0]["title"], "Первая")

    def test_invalid_task_or_id_does_not_write(self):
        """Без даты, названия или UUID задача не попадает в хранилище."""
        day = self.store.clock().date().isoformat()
        with self.assertRaises(ValueError):
            self.tools.create_task("not-a-uuid", "Задача", day)
        with self.assertRaises(RuntimeError):
            self.tools.create_task(str(uuid.uuid4()), "", day)
        with self.assertRaises(RuntimeError):
            self.tools.create_task(str(uuid.uuid4()), "Будильник", day,
                                   reminder=True, time="25:00")
        self.assertEqual(self.store.list_tasks(), [])

    def test_default_time_and_event_options_through_api(self):
        from datetime import date, timedelta
        day = self.store.clock().date().isoformat()
        event = (date.fromisoformat(day) + timedelta(days=4)).isoformat()
        result = self.tools.create_task(str(uuid.uuid4()), "Событие", day,
                                       reminder=True, recurrence="event", event_date=event,
                                       reminder_end_date=event, escalation_days=2)
        self.assertEqual(result["task"]["time"], "09:00")
        self.assertEqual(result["task"]["event_date"], event)
        self.assertEqual(result["task"]["escalation_days"], 2)

    def test_missing_profile_has_actionable_error(self):
        """Выключенное приложение не даёт обещания о сохранённой задаче."""
        with self.assertRaisesRegex(RuntimeError, "Сначала запусти"):
            GptTools(self.directory / "missing").context()

    def test_setup_writes_paths_for_settings_without_changing_user_config(self):
        """Фрагмент подключения остаётся в проекте и содержит точные пути."""
        directory = self.directory / "Project with spaces"
        directory.mkdir()
        executable = directory / ".venv-gpt" / "Scripts" / "python.exe"
        write_connection(directory, executable)
        config = json.loads((directory / "GPT_MCP_CONNECTION.json").read_text())
        self.assertEqual(config["command"], str(executable.resolve()))
        self.assertEqual(config["args"], [str((directory / "gpt_mcp.py").resolve())])
        self.assertNotIn(self.token, json.dumps(config))

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK необязателен для Windows EXE")
    def test_real_stdio_mcp_handshake_and_task_roundtrip(self):
        """SDK-клиент читает каталог и записывает задачу реальным инструментом."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def run():
            """Проверить тот же STDIO-процесс, который запускает ChatGPT."""
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(Path(__file__).parents[1] / "gpt_mcp.py"),
                      "--data-dir", str(self.directory)],
            )
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    catalog = await session.list_tools()
                    self.assertEqual({t.name for t in catalog.tools}, {
                        "ritm_get_context", "ritm_list_day", "ritm_create_task",
                    })
                    context = await session.call_tool("ritm_get_context", {})
                    self.assertFalse(context.isError)
                    arguments = {
                        "request_id": str(uuid.uuid4()), "title": "Из ChatGPT",
                        "start_date": self.store.clock().date().isoformat(),
                        "time": "19:00", "reminder": True,
                    }
                    for _ in range(2):
                        response = await session.call_tool("ritm_create_task", arguments)
                        self.assertFalse(response.isError, response)
                        result = response.structuredContent or json.loads(response.content[0].text)
                        self.assertTrue(result["saved"])
                    for recurrence in (*HOUR_INTERVALS, "every_3_days", "monthly"):
                        interval = dict(arguments, request_id=str(uuid.uuid4()),
                                        recurrence=recurrence)
                        response = await session.call_tool("ritm_create_task", interval)
                        self.assertFalse(response.isError, response)
                    arguments["request_id"] = str(uuid.uuid4())
                    arguments["reminder"] = "true"
                    invalid = await session.call_tool("ritm_create_task", arguments)
                    self.assertTrue(invalid.isError)
                    self.assertNotIn(self.token, context.model_dump_json())
        asyncio.run(run())
        self.assertEqual(len(self.store.list_tasks()), 8)
