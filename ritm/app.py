"""Сборка локального приложения и управление его жизненным циклом."""

import argparse
import json
import logging
import os
import secrets
import signal
import sys
import threading
from pathlib import Path

from .config import DEFAULT_PORT, api_token, data_directory
from .platform import InstanceLock, open_window, running_instance, set_autostart
from .scheduler import Scheduler
from .server import LocalServer, WEB_ROOT
from .store import Store
from .tray import WindowsTray
from .windows_identity import WindowIdentity, install_shortcuts
from .mobile_sync import SyncService
from .usb_sync import start as start_usb_sync
from .notifications import WindowsNotifications


LOGGER = logging.getLogger(__name__)


def main():
    """Запустить один процесс с API, планировщиком и значком в трее."""
    parser = argparse.ArgumentParser(description="Ритм · задачи и привычки")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--install-shortcuts", action="store_true")
    arguments = parser.parse_args()
    if arguments.install_shortcuts:
        executable = (Path(sys.executable) if getattr(sys, "frozen", False)
                      else Path(__file__).resolve().parents[1] / "dist" / "Ritm.exe")
        paths = install_shortcuts(executable)
        if os.name == "nt":
            import ctypes
            message = ctypes.WinDLL("user32").MessageBoxW
            message.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                ctypes.c_wchar_p, ctypes.c_uint]
            message.restype = ctypes.c_int
            message(None, "Ярлыки созданы:\n" + "\n".join(map(str, paths))
                    + "\n\nНайди Ритм в меню Пуск. Для панели задач нажми "
                    "правой кнопкой → Закрепить на панели задач.", "Ритм", 0x40)
        return 0
    if not 0 <= arguments.port <= 65535:
        parser.error("port должен быть от 0 до 65535")
    directory = arguments.data_dir.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(directory / "ritm.log", encoding="utf-8")],
    )
    lock = InstanceLock(directory)
    if not lock.acquire():
        existing = running_instance(directory)
        lock.close()
        if existing and not arguments.background:
            url = f"http://127.0.0.1:{existing['port']}/open/{existing['session']}"
            open_window(url, directory)
        return 0 if existing else 1
    server = None
    server_thread = None
    tray = None
    mobile_sync = None
    notifications = None
    stop_event = threading.Event()
    try:
        token = api_token(directory)
        session = secrets.token_urlsafe(32)
        store = Store(directory / "ritm.sqlite3")
        mobile_sync = SyncService(store, directory)
        start_usb_sync(directory, stop_event)
        window_identity = WindowIdentity(directory, stop_event)
        if not arguments.no_tray:
            notifications = WindowsNotifications(directory, WEB_ROOT / "icon.ico")
            notifications.initialize()

        def notify(title, body):
            """Отправить уведомление, сохраняя настройки звука."""
            if notifications:
                return notifications.notify(title, body, store.settings()["sound"])
            return {"accepted": False, "message": "Системные уведомления отключены в этом запуске."}

        def status():
            """Сообщить интерфейсу фактические возможности текущей ОС."""
            return {
                "platform": os.name, "tray": bool(tray and tray.available),
                "tray_error": tray.error if tray else "Трей отключён.",
                "notifications_available": bool(notifications and notifications.available),
                "notification_error": notifications.error if notifications else "Системные уведомления отключены.",
                "autostart_supported": os.name == "nt",
                "data_directory": str(directory),
            }

        actions = {
            "exit_fullscreen": window_identity.exit_fullscreen,
            "mobile_status": mobile_sync.status,
            "mobile_configure": mobile_sync.configure,
            "notify": notify, "status": status, "quit": stop_event.set,
            "autostart": lambda enabled: set_autostart(
                enabled, directory, server.server_port
            ),
        }
        server = LocalServer(("127.0.0.1", arguments.port), store,
                             token, session, actions)
        open_url = server.base_url + "/open/" + session
        if not arguments.no_tray:
            tray = WindowsTray(WEB_ROOT / "icon.ico",
                               lambda: open_window(open_url, directory),
                               stop_event.set)
            tray.start()
            tray.ready.wait(3)
        server_thread = threading.Thread(
            target=server.serve_forever, name="ritm-http", daemon=True
        )
        server_thread.start()
        window_identity.start()
        runtime = {
            "pid": os.getpid(), "port": server.server_port,
            "session": session,
        }
        temporary = directory / "runtime.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                             0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(runtime, stream)
        temporary.replace(directory / "runtime.json")
        scheduler = Scheduler(store, notify, stop_event)
        scheduler.start()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop_event.set())
        LOGGER.info("Ритм запущен. Порт %s, tray=%s", server.server_port,
                    bool(tray and tray.available))
        if not arguments.background:
            open_window(open_url, directory)
        while not stop_event.wait(1):
            pass
        LOGGER.info("Остановка Ритма")
        scheduler.join(timeout=8)
        return 0
    except Exception:
        LOGGER.exception("Не удалось запустить Ритм")
        return 1
    finally:
        stop_event.set()
        if mobile_sync:
            mobile_sync.close()
        if tray:
            tray.close()
            tray.join(timeout=2)
        if server:
            if server_thread and server_thread.is_alive():
                server.shutdown()
            server.server_close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
