"""Системные уведомления Windows 10/11 с собственной постоянной идентичностью.

WinRT вызывается через встроенный Windows PowerShell, без pip и внешних пакетов.
Текст передаётся как JSON через stdin, а не исполняется как PowerShell-код.
"""

import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET

from .windows_identity import APP_ID, TOAST_CLSID, install_shortcuts


LOGGER = logging.getLogger(__name__)
SEND_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
    [Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
    [Windows.UI.Notifications.NotificationSetting,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
    [Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] > $null
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($payload.app_id)
    # The explicit getter propagates WinRT registration errors; PS property access can hide them.
    $setting = 0
    try { $setting = [int]$notifier.get_Setting() } catch {
        # A new app has no notification channel until its first Show().
        if ($_.Exception.GetBaseException().HResult -ne -2147023728) { throw }
    }
    if ($setting -ne 0) {
        @{accepted=$false; setting=$setting} | ConvertTo-Json -Compress
        exit 0
    }
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml($payload.xml)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    $toast.Tag = $payload.tag
    $toast.Group = 'reminders'
    $notifier.Show($toast)
    $setting = [int]$notifier.get_Setting()
    if ($setting -ne 0) {
        @{accepted=$false; setting=$setting} | ConvertTo-Json -Compress
        exit 0
    }
    @{accepted=$true; setting=0} | ConvertTo-Json -Compress
} catch {
    @{accepted=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
"""

BLOCKED = {
    1: "В Windows отключены уведомления для Ритма. Разреши их в настройках уведомлений Windows.",
    2: "В Windows отключены уведомления для пользователя.",
    3: "Уведомления запрещены политикой Windows.",
    4: "Windows не разрешает уведомления для этой регистрации приложения.",
}


def toast_xml(title, body, sound):
    """Экранировать текст и открывать Ритм по нажатию без хранения токенов API."""
    toast = ET.Element("toast", activationType="protocol", launch="ritm://open")
    binding = ET.SubElement(ET.SubElement(toast, "visual"), "binding", template="ToastGeneric")
    ET.SubElement(binding, "text").text = title
    ET.SubElement(binding, "text").text = body
    if not sound:
        ET.SubElement(toast, "audio", silent="true")
    return ET.tostring(toast, encoding="unicode")


def launch_arguments(directory):
    """Хранить путь постоянного EXE, а не временной распаковки PyInstaller."""
    executable = Path(sys.executable).resolve()
    args = []
    if not getattr(sys, "frozen", False):
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            executable = windowless
        args.append(str(Path(__file__).resolve().parents[1] / "main.py"))
    args += ["--data-dir", str(directory)]
    return executable, args


class WindowsNotifications:
    """Отделить наличие значка трея от возможности отправить уведомление."""

    def __init__(self, directory, icon_path):
        self.directory = Path(directory)
        self.icon_path = Path(icon_path)
        self.available = False
        self.error = ""
        self.last_result = None
        self.lock = threading.Lock()

    def initialize(self):
        if os.name != "nt":
            self.error = "Системные уведомления доступны в Windows."
            return
        try:
            import winreg
            import ctypes
            from ctypes import wintypes

            executable, args = launch_arguments(self.directory)
            icon = self.directory / "notification.ico"
            shutil.copyfile(self.icon_path, icon)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\Classes\\AppUserModelId\\" + APP_ID) as key:
                for name, value in (("DisplayName", "Ритм"), ("IconUri", str(icon)),
                                    ("CustomActivator", "{" + TOAST_CLSID + "}")):
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            # Protocol activation works after the sender exits; never interpolate a URI into a command.
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\Classes\\ritm") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Ритм")
                winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\Classes\\ritm\\shell\\open\\command") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ,
                                 subprocess.list2cmdline([str(executable), *args]))
            install_shortcuts(executable, desktop=False,
                              arguments=subprocess.list2cmdline(args), toast=True)
            shell = ctypes.WinDLL("shell32")
            shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
            shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
            result = shell.SetCurrentProcessExplicitAppUserModelID(APP_ID)
            if result < 0:
                raise OSError(f"AppUserModelID HRESULT 0x{result & 0xffffffff:08x}")
            self.available = True
        except Exception as error:
            self.error = str(error)
            LOGGER.exception("Не удалось зарегистрировать уведомления Windows")

    def notify(self, title, body, sound=True):
        """Вернуть подтверждение Windows или конкретную причину отказа."""
        if not self.available:
            return {"accepted": False, "message": self.error or "Системные уведомления недоступны."}
        with self.lock:
            try:
                executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
                command = base64.b64encode(SEND_SCRIPT.encode("utf-16-le")).decode("ascii")
                payload = {"app_id": APP_ID, "xml": toast_xml(title, body, sound),
                           "tag": uuid.uuid4().hex[:16]}
                completed = subprocess.run(
                    [str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                    input=json.dumps(payload, ensure_ascii=False), encoding="utf-8", errors="replace",
                    capture_output=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                result = json.loads(completed.stdout.lstrip("\ufeff").strip())
                if result.get("accepted") is True and completed.returncode == 0:
                    result["message"] = "Windows приняла уведомление Ритма. Проверь центр уведомлений; режим «Не беспокоить» может скрывать баннер."
                    self.error = ""
                    LOGGER.info("Windows приняла системное уведомление Ритма")
                else:
                    result["accepted"] = False
                    result["message"] = BLOCKED.get(result.get("setting"), result.get("error") or "Windows не приняла уведомление.")
                    self.error = result["message"]
                    LOGGER.warning("Не удалось отправить уведомление: %s", self.error)
            except Exception as error:
                self.error = "Не удалось отправить системное уведомление: " + str(error)
                LOGGER.exception("Ошибка отправки уведомления Windows")
                result = {"accepted": False, "message": self.error}
            self.last_result = result
            return result
