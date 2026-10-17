"""Интеграционные проверки настоящего HTTP API на свободном порту."""

import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from integrations.dictor_client import RitmClient
from ritm.server import LocalServer, WEB_ROOT, load_web_assets
from ritm.store import Store


class ApiTests(unittest.TestCase):
    """Проверять авторизацию, сессию UI и команды внешнего клиента."""

    def setUp(self):
        """Поднять изолированное приложение без системных изменений."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / "test.sqlite3")
        self.token = "test-api-token-" + "a" * 32
        self.session = "test-ui-session-" + "b" * 32
        self.messages = []
        self.server = LocalServer(("127.0.0.1", 0), self.store,
                                  self.token, self.session, {
            "status": lambda: {"platform": "test", "tray": False},
            "notify": lambda *item: self.messages.append(item),
            "quit": lambda: None, "autostart": lambda value: None,
            "exit_fullscreen": lambda: {"exited": True},
        })
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        """Завершить сервер до удаления временной базы."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, auth=True, headers=None):
        """Отправить настоящий loopback HTTP-запрос."""
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        request_headers = dict(headers or {})
        if auth:
            request_headers["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=data, headers=request_headers)
        response = connection.getresponse()
        content = response.read()
        result = {
            "status": response.status, "headers": dict(response.getheaders()),
            "body": json.loads(content) if content and response.getheader(
                "Content-Type", ""
            ).startswith("application/json") else content,
        }
        connection.close()
        return result

    def test_health_is_public_but_data_requires_authentication(self):
        """Проверка процесса не открывает приватные задачи."""
        self.assertEqual(self.request("GET", "/api/v1/health", auth=False)["status"], 200)
        self.assertEqual(self.request("GET", "/api/v1/state", auth=False)["status"], 401)
        self.assertEqual(self.request("GET", "/api/v1/state")["status"], 200)

    def test_exit_fullscreen_requires_local_authenticated_request(self):
        """Esc не открывает доступ к управлению окном посторонним сайтам."""
        path = "/api/v1/app/exit-fullscreen"
        self.assertEqual(self.request("POST", path, {}, auth=False)["status"], 401)
        self.assertEqual(self.request("POST", path, {}, headers={
            "Origin": "https://other.invalid"})["status"], 403)
        self.assertEqual(self.request("POST", path, {})["body"], {"exited": True})

    def test_dismiss_endpoint_checks_authentication_deadline_and_undo(self):
        now = datetime.now().astimezone().replace(hour=21,minute=0,second=0,microsecond=0)
        self.store.clock = lambda: now
        self.store.create_task({"title":"Лекция"})
        row = self.store.state(now.date().isoformat())["tasks"][0]
        path = f"/api/v1/occurrences/{row['id']}/dismiss"
        self.assertEqual(self.request("POST", path, {}, auth=False)["status"],401)
        self.assertEqual(self.request("POST", path, {})["status"],409)
        now += timedelta(hours=5)
        self.assertIsNotNone(self.request("POST", path, {})["body"]["dismissed_at"])
        self.assertEqual(self.store.state(now.date().isoformat())["summary"]["xp"],0)
        self.assertIsNone(self.request("POST", path, {"dismissed":False})["body"]["dismissed_at"])

    def test_checkin_endpoint_updates_only_the_selected_slot(self):
        now=datetime.now().astimezone().replace(hour=12,minute=0,second=0,microsecond=0)
        self.store.clock=lambda:now
        self.store.create_task(dict(title="Спорт",time="09:00",recurrence="every_3_hours",reminder=True))
        row=self.store.state(now.date().isoformat())["tasks"][0]
        path=f"/api/v1/occurrences/{row['id']}/check"
        self.assertEqual(self.request("POST",path,{"time":"09:00"},auth=False)["status"],401)
        self.assertEqual(self.request("POST",path,{"time":"15:00"})["status"],400)
        self.assertEqual(self.request("POST",path,{"time":"09:00"})["status"],200)
        self.assertEqual(self.store.state(now.date().isoformat())["summary"]["xp"],10)

    def test_ui_session_uses_http_only_same_site_cookie(self):
        """Запуск выдаёт cookie и убирает секретный URL редиректом."""
        result = self.request("GET", "/open/" + self.session, auth=False)
        self.assertEqual(result["status"], 303)
        self.assertEqual(result["headers"]["Location"], "/")
        cookie = result["headers"]["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        state = self.request("GET", "/api/v1/state", auth=False,
                             headers={"Cookie": cookie.split(";")[0]})
        self.assertEqual(state["status"], 200)

    def test_cross_origin_and_rebinding_host_are_rejected(self):
        """Внешняя вкладка не может послать локальную команду."""
        for headers in (
            {"Origin": "https://example.com"},
            {"Host": "attacker.example"}, {"Sec-Fetch-Site": "cross-site"},
        ):
            result = self.request("POST", "/api/v1/tasks", {"title": "x"},
                                  headers=headers)
            self.assertEqual(result["status"], 403)
        self.assertEqual(self.store.list_tasks(), [])

    def test_non_json_mutation_is_rejected(self):
        """HTML-форма без JSON не проходит до изменения базы."""
        result = self.request("POST", "/api/v1/tasks", {"title": "x"},
                              headers={"Content-Type": "text/plain"})
        self.assertEqual(result["status"], 415)

    def test_dictor_client_creates_once_and_completes_once(self):
        """Клиент Dictor проходит полный цикл через рабочий API."""
        client = RitmClient(self.server.base_url, self.token)
        first = client.create_task("Голосовая задача", request_id="utterance-1")
        second = client.create_task("Голосовая задача", request_id="utterance-1")
        self.assertEqual(first["task"]["id"], second["task"]["id"])
        current = client.state()
        self.assertEqual(len(current["tasks"]), 1)
        self.assertTrue(current["dictor_last_seen"])
        occurrence_id = current["tasks"][0]["id"]
        client.complete_task(occurrence_id)
        client.complete_task(occurrence_id)
        self.assertEqual(client.state()["summary"]["xp"], 10)

    def test_dictor_rejects_shell_and_missing_request_id(self):
        """LLM не может использовать API для произвольного исполнения."""
        for body in (
            {"action": "shell.execute", "code": "whoami"},
            {"action": "task.create", "task": {"title": "x"}},
            {"action": "task.complete", "occurrence_id": "1"},
        ):
            self.assertEqual(self.request("POST", "/api/v1/commands", body)["status"], 400)

    def test_static_assets_and_calendar_are_available(self):
        """Поставляемый интерфейс и его данные не содержат пропавших файлов."""
        for path in (
            "/", "/app.js", "/theme.js", "/styles.css", "/themes.css",
            "/icon.svg", "/favicon.ico", "/icon.png",
        ):
            with self.subTest(path=path):
                result = self.request("GET", path, auth=False)
                self.assertEqual(result["status"], 200)
                self.assertGreater(len(result["body"]), 20)
        result = self.request("GET", "/api/v1/calendar")
        self.assertEqual(len(result["body"]["days"]), 42)
        self.assertEqual(self.request("GET", "/../config.py", auth=False)["status"], 404)

    def test_png_brand_icon_is_served_with_correct_type(self):
        """PNG виден браузеру и совпадает с упакованным изображением."""
        result = self.request("GET", "/icon.png", auth=False)
        self.assertEqual(result["status"], 200)
        self.assertTrue(result["headers"]["Content-Type"].startswith("image/png"))
        self.assertTrue(result["body"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(result["body"], (WEB_ROOT / "icon.png").read_bytes())

    def test_theme_settings_roundtrip_through_api(self):
        """Сохранённая тема возвращается через API, некорректная отклоняется."""
        values = {"theme": "midnight", "accent_color": "#A08BFF",
                  "neon_intensity": 25}
        response = self.request("PATCH", "/api/v1/settings", values)
        self.assertEqual(response["status"], 200)
        state = self.request("GET", "/api/v1/state")["body"]
        self.assertEqual(state["settings"]["theme"], "midnight")
        self.assertEqual(state["settings"]["accent_color"], "#a08bff")
        invalid = self.request("PATCH", "/api/v1/settings",
                               {"accent_color": "#fff; color: red"})
        self.assertEqual(invalid["status"], 400)
        asset = self.request("GET", "/theme.js", auth=False)
        self.assertTrue(asset["headers"]["Content-Type"].startswith(
            "text/javascript"
        ))

    def test_notification_test_is_logged(self):
        """Проверочное уведомление имеет реальное событие и callback."""
        result = self.request("POST", "/api/v1/notification-test", {})
        self.assertEqual(result["status"], 200)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(len(self.request("GET", "/api/v1/state")["body"]["notifications"]), 1)

    def test_notification_test_returns_actual_windows_rejection(self):
        self.server.actions["notify"] = lambda *_: {"accepted": False, "message": "Windows запретила уведомления"}
        result = self.request("POST", "/api/v1/notification-test", {})
        self.assertFalse(result["body"]["delivery"]["accepted"])
        self.assertEqual(result["body"]["delivery"]["message"], "Windows запретила уведомления")

    def test_day_review_returns_titles_and_requires_authorization(self):
        day = self.store.clock().date().isoformat()
        self.store.create_task({"title": "Лекция <физика>"})
        response = self.request("GET", "/api/v1/progress/day?date=" + day)
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"]["unfinished"][0]["title"], "Лекция <физика>")
        self.assertEqual(self.request("GET", "/api/v1/progress/day?date=" + day, auth=False)["status"], 401)
        self.assertEqual(self.request("GET", "/api/v1/progress/day?date=wrong")["status"], 400)

    def test_task_editor_roundtrip_through_ui_cookie(self):
        """Редактор изменяет все поля через ту же сессию, что и окно."""
        cookie = {"Cookie": "ritm_session=" + self.session}
        task = self.request("POST", "/api/v1/tasks", {"title": "Было"},
                            auth=False, headers=cookie)["body"]
        fields = {"title": "Стало", "notes": "Моя заметка", "category": "Учёба",
                  "time": "20:30", "reminder": True, "recurrence": "daily",
                  "priority": "high"}
        response = self.request("PATCH", "/api/v1/tasks/" + task["id"], fields,
                                auth=False, headers=cookie)
        self.assertEqual(response["status"], 200)
        current = self.request("GET", "/api/v1/state", auth=False,
                               headers=cookie)["body"]["tasks"][0]
        for key, value in fields.items():
            self.assertEqual(current[key], value)

    def test_theme_is_included_in_primary_assets_and_not_cached(self):
        """UI не зависит от отдельной загрузки файлов оформления."""
        style = self.request("GET", "/styles.css?v=0.2.0", auth=False)
        script = self.request("GET", "/app.js?v=0.2.0", auth=False)
        self.assertIn(b".palette-controls", style["body"])
        self.assertTrue(style["headers"]["Content-Type"].startswith("text/css"))
        self.assertEqual(style["headers"]["Cache-Control"], "no-store")
        self.assertIn(b"window.RitmTheme =", script["body"])
        self.assertLess(script["body"].index(b"window.RitmTheme ="),
                        script["body"].index(b"function render_appearance"))

    def test_missing_theme_resource_prevents_partial_ui_startup(self):
        """Пропавший файл темы явно останавливает запуск сервера."""
        original = Path.is_file
        def available(path):
            """Имитировать ровно один пропавший ресурс."""
            return False if path == WEB_ROOT / "themes.css" else original(path)
        with patch.object(Path, "is_file", available):
            with self.assertRaisesRegex(RuntimeError, "themes.css"):
                load_web_assets()

    def test_request_key_conflict_and_unknown_api_have_correct_status(self):
        """Повторный ID с другими данными возвращает конфликт."""
        headers = {"Idempotency-Key": "same"}
        self.request("POST", "/api/v1/tasks", {"title": "a"}, headers=headers)
        result = self.request("POST", "/api/v1/tasks", {"title": "b"}, headers=headers)
        self.assertEqual(result["status"], 409)
        self.assertEqual(self.request("GET", "/api/v1/no-such-method")["status"], 404)

    def test_export_does_not_contain_authentication_secrets(self):
        """Скачиваемый экспорт не передаёт bearer или UI-сессию."""
        result = self.request("GET", "/api/v1/export")
        serialized = json.dumps(result["body"])
        self.assertNotIn(self.token, serialized)
        self.assertNotIn(self.session, serialized)
        self.assertIn("attachment", result["headers"]["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
