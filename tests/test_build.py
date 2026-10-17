"""Проверки путей EXE и протокола проверки собранного приложения."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_windows
from ritm.platform import autostart_command
from ritm.store import ValidationError


class BuildTests(unittest.TestCase):
    """Проверять поведение сборки без имитации успешной Windows-компиляции."""

    def test_frozen_autostart_uses_permanent_executable(self):
        """Автозапуск не должен ссылаться на временную распаковку onefile."""
        executable = str(Path("Apps With Spaces") / "Ritm.exe")
        directory = Path("User Data")
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", executable
        ), patch.object(sys, "_MEIPASS", "temporary-bundle", create=True):
            command = autostart_command(directory, 8768)
        expected = subprocess.list2cmdline([
            executable, "--background", "--data-dir", str(directory),
            "--port", "8768",
        ])
        self.assertEqual(command, expected)
        self.assertNotIn("temporary-bundle", command)
        self.assertNotIn("main.py", command)
        self.assertNotIn("pythonw.exe", command)

    def test_source_autostart_keeps_pythonw_entry(self):
        """Обычный запуск исходников продолжает использовать pythonw."""
        with tempfile.TemporaryDirectory() as temporary:
            python = Path(temporary) / "python.exe"
            windowless = python.with_name("pythonw.exe")
            windowless.touch()
            with patch.object(sys, "frozen", False, create=True), patch.object(
                sys, "executable", str(python)
            ):
                command = autostart_command(Path(temporary), 8769)
            self.assertIn("pythonw.exe", command)
            self.assertIn("main.py", command)
            self.assertIn("8769", command)

    def test_source_autostart_reports_missing_pythonw(self):
        """Неполная установка Python даёт понятную ошибку."""
        with tempfile.TemporaryDirectory() as temporary:
            python = str(Path(temporary) / "python.exe")
            with patch.object(sys, "frozen", False, create=True), patch.object(
                sys, "executable", python
            ), self.assertRaises(ValidationError):
                autostart_command(Path(temporary), 8768)

    def test_packaged_runtime_check_protocol_against_real_source_server(self):
        """Проверить протокол smoke-test на настоящем API исходной версии."""
        real_popen = subprocess.Popen
        entry = build_windows.ROOT / "main.py"

        def source_process(arguments, **kwargs):
            """Подставить Python только вместо ещё не собранного bootloader."""
            return real_popen(
                [sys.executable, str(entry), *arguments[1:]],
                cwd=build_windows.ROOT,
            )

        with patch.object(build_windows.subprocess, "Popen", source_process):
            build_windows.smoke_test(Path("not-a-built-exe") / "Ritm.exe")

    @unittest.skipIf(sys.platform == "win32", "Только защита от cross-build")
    def test_windows_build_is_rejected_on_other_platforms(self):
        """Linux-сборка не должна выдавать Linux ELF за Windows EXE."""
        with self.assertRaisesRegex(RuntimeError, "on Windows"):
            build_windows.validate_environment()


if __name__ == "__main__":
    unittest.main()
