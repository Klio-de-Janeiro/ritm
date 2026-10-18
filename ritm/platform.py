"""Окно приложения, единственный процесс и автозапуск Windows."""

import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from .store import ValidationError


class InstanceLock:
    """Межпроцессная блокировка, освобождаемая самой ОС."""

    def __init__(self, directory):
        """Открыть один байт в каталоге данных текущего пользователя."""
        self.stream = (directory / ".instance.lock").open("a+b")
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        self.held = False

    def acquire(self):
        """Вернуть false, если приложение уже работает."""
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self.held = True
        return True

    def close(self):
        """Освободить собственную блокировку и файловый дескриптор."""
        if self.held:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_UN)
            self.held = False
        self.stream.close()


def running_instance(directory):
    """Прочитать runtime-файл только при подтверждённом локальном API."""
    try:
        runtime = json.loads((directory / "runtime.json").read_text("utf-8"))
        port = runtime["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        opener = build_opener(ProxyHandler({}))
        request = Request(f"http://127.0.0.1:{port}/api/v1/health")
        with opener.open(request, timeout=1) as response:
            if json.load(response).get("app") != "ritm":
                return None
        return runtime
    except (OSError, ValueError, KeyError, TypeError):
        return None


def browser_executable():
    """Найти установленный Edge или Chrome без изменения браузера."""
    if os.name != "nt":
        return None
    for executable in ("msedge.exe", "chrome.exe"):
        located = shutil.which(executable)
        if located:
            return located
    relatives = (
        "Microsoft/Edge/Application/msedge.exe",
        "Google/Chrome/Application/chrome.exe",
    )
    for relative in relatives:
        for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(key)
            if base and (Path(base) / relative).is_file():
                return str(Path(base) / relative)
    return None


def open_window(url, directory):
    """Открыть UI в app-окне Chromium или в обычном браузере."""
    executable = browser_executable()
    if executable:
        subprocess.Popen([
            executable, "--app=" + url,
            "--user-data-dir=" + str(directory / "browser-profile"),
            "--no-first-run", "--start-maximized",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url, new=1)


def autostart_command(directory, port):
    """Ссылаться на постоянный EXE либо на Python исходной установки."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        arguments = [str(executable)]
    else:
        windowless = executable.with_name("pythonw.exe")
        if not windowless.is_file():
            raise ValidationError("Для тихого автозапуска нужен pythonw.exe.")
        entry = Path(__file__).resolve().parents[1] / "main.py"
        arguments = [str(windowless), str(entry)]
    return subprocess.list2cmdline(arguments + [
        "--background",
        "--data-dir", str(directory), "--port", str(port),
    ])


def set_autostart(enabled, directory, port):
    """Включить или выключить только собственную запись HKCU Run."""
    if os.name != "nt":
        if enabled:
            raise ValidationError("Автозапуск этой сборки доступен в Windows.")
        return
    import winreg

    command = autostart_command(directory, port) if enabled else None
    key_name = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_name) as key:
        if enabled:
            winreg.SetValueEx(key, "Ritm", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "Ritm")
            except FileNotFoundError:
                pass
