"""Доставка, запреты Windows и безопасная передача текста уведомлений."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from ritm.notifications import WindowsNotifications, launch_arguments, toast_xml


class NotificationTests(unittest.TestCase):
    def service(self):
        service = WindowsNotifications(Path("unused-profile"), Path("icon.ico"))
        service.available = True
        return service

    def test_user_text_is_data_and_silent_audio_is_explicit(self):
        title = 'Спорт <важно> & "готово"; $(exit)'
        body = "Кириллица 🏃\nСледующий шаг"
        xml = ET.fromstring(toast_xml(title, body, False))
        self.assertEqual([node.text for node in xml.findall("./visual/binding/text")], [title, body])
        self.assertEqual(xml.attrib["launch"], "ritm://open")
        self.assertEqual(xml.find("audio").attrib, {"silent": "true"})
        self.assertIsNone(ET.fromstring(toast_xml(title, body, True)).find("audio"))

    def test_delivery_uses_json_stdin_and_requires_windows_acknowledgment(self):
        service = self.service()
        response = subprocess.CompletedProcess([], 0, json.dumps({"accepted": True, "setting": 0}), "")
        with patch("ritm.notifications.subprocess.run", return_value=response) as run:
            result = service.notify('Кавычки " и $()', "Текст", False)
        self.assertTrue(result["accepted"])
        args, options = run.call_args
        self.assertNotIn("Кавычки", " ".join(args[0]))
        self.assertIn("Кавычки", json.loads(options["input"])["xml"])
        self.assertTrue(options["capture_output"])
        self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_windows_block_and_process_failure_are_not_reported_as_success(self):
        service = self.service()
        for code, payload in ((0, {"accepted": False, "setting": 1}),
                              (1, {"accepted": True}),
                              (1, {"accepted": False, "error": "Ошибка WinRT"})):
            with self.subTest(payload=payload), patch("ritm.notifications.subprocess.run",
                    return_value=subprocess.CompletedProcess([], code, json.dumps(payload), "")):
                result = service.notify("Проверка", "Текст")
                self.assertFalse(result["accepted"])
                self.assertTrue(service.error)
        with patch("ritm.notifications.subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 12)):
            self.assertFalse(service.notify("Проверка", "Текст")["accepted"])

    def test_missing_notification_service_does_not_claim_delivery(self):
        service = WindowsNotifications(Path("unused-profile"), Path("icon.ico"))
        service.error = "Регистрация недоступна"
        self.assertFalse(service.notify("Проверка", "Текст")["accepted"])

    def test_frozen_activation_keeps_the_permanent_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "App With Spaces" / "Ritm.exe"
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)):
                target, args = launch_arguments(Path(folder))
        self.assertEqual(target, executable)
        self.assertEqual(args, ["--data-dir", folder])
        self.assertNotIn("_MEI", str(target))
