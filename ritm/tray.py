"""Значок и системные напоминания Windows через ctypes, без pip."""

import logging
import os
import queue
import threading


LOGGER = logging.getLogger(__name__)


class WindowsTray(threading.Thread):
    """Владеть нативным message loop и очередью уведомлений."""

    def __init__(self, icon_path, on_open, on_quit):
        """Подготовить callbacks; все Win32-вызовы идут в одном потоке."""
        super().__init__(name="ritm-tray", daemon=True)
        self.icon_path = icon_path
        self.on_open = on_open
        self.on_quit = on_quit
        self.messages = queue.Queue()
        self.ready = threading.Event()
        self.available = False
        self.error = ""
        self.hwnd = None

    def notify(self, title, body, sound=True):
        """Поставить напоминание в очередь потока Windows."""
        if self.available:
            self.messages.put((title, body, sound))

    def close(self):
        """Попросить собственное окно завершить цикл."""
        if self.available and self.hwnd:
            import ctypes
            from ctypes import wintypes

            post = ctypes.windll.user32.PostMessageW
            post.argtypes = [wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM]
            post(self.hwnd, 0x0010, 0, 0)

    def run(self):
        """Поднять значок; оставить локальный журнал при ошибке ОС."""
        if os.name != "nt":
            self.error = "Системные уведомления реализованы для Windows."
            self.ready.set()
            return
        try:
            self._windows_loop()
        except Exception as error:
            self.error = str(error)
            LOGGER.exception("Не удалось запустить Windows tray")
        finally:
            self.available = False
            self.ready.set()

    def _windows_loop(self):
        """Создать скрытое окно, значок, меню и очередь shell-уведомлений."""
        import ctypes
        from ctypes import wintypes

        user = ctypes.WinDLL("user32", use_last_error=True)
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        result_type = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(
            result_type, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )

        class WindowClass(ctypes.Structure):
            """Win32 WNDCLASSW с корректными указателями x64."""

            _fields_ = [
                ("style", wintypes.UINT), ("proc", callback_type),
                ("class_extra", ctypes.c_int), ("window_extra", ctypes.c_int),
                ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON),
                ("cursor", wintypes.HANDLE), ("background", wintypes.HBRUSH),
                ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR),
            ]

        class Guid(ctypes.Structure):
            """GUID, входящий в NOTIFYICONDATAW."""

            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD),
                        ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        class NotifyData(ctypes.Structure):
            """Полная Unicode-структура Windows Vista+ Shell_NotifyIcon."""

            _fields_ = [
                ("size", wintypes.DWORD), ("hwnd", wintypes.HWND),
                ("id", wintypes.UINT), ("flags", wintypes.UINT),
                ("callback", wintypes.UINT), ("icon", wintypes.HICON),
                ("tip", wintypes.WCHAR * 128), ("state", wintypes.DWORD),
                ("state_mask", wintypes.DWORD), ("info", wintypes.WCHAR * 256),
                ("timeout", wintypes.UINT), ("title", wintypes.WCHAR * 64),
                ("info_flags", wintypes.DWORD), ("guid", Guid),
                ("balloon_icon", wintypes.HICON),
            ]

        declarations = (
            (kernel.GetModuleHandleW, [wintypes.LPCWSTR], wintypes.HMODULE),
            (user.RegisterClassW, [ctypes.POINTER(WindowClass)], wintypes.ATOM),
            (user.CreateWindowExW, [wintypes.DWORD, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID],
             wintypes.HWND),
            (user.DefWindowProcW, [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM], result_type),
            (user.LoadImageW, [wintypes.HINSTANCE, wintypes.LPCWSTR,
                              wintypes.UINT, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT], wintypes.HANDLE),
            (user.CreatePopupMenu, [], wintypes.HMENU),
            (user.AppendMenuW, [wintypes.HMENU, wintypes.UINT,
                               ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL),
            (user.TrackPopupMenu, [wintypes.HMENU, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, wintypes.LPVOID], wintypes.UINT),
            (user.DestroyMenu, [wintypes.HMENU], wintypes.BOOL),
            (user.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL),
            (user.GetCursorPos, [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            (user.SetTimer, [wintypes.HWND, ctypes.c_size_t, wintypes.UINT,
                             wintypes.LPVOID], ctypes.c_size_t),
            (user.KillTimer, [wintypes.HWND, ctypes.c_size_t], wintypes.BOOL),
            (user.GetMessageW, [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT], wintypes.BOOL),
            (user.TranslateMessage, [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
            (user.DispatchMessageW, [ctypes.POINTER(wintypes.MSG)], result_type),
            (user.DestroyWindow, [wintypes.HWND], wintypes.BOOL),
            (user.DestroyIcon, [wintypes.HICON], wintypes.BOOL),
            (user.PostQuitMessage, [ctypes.c_int], None),
            (user.RegisterWindowMessageW, [wintypes.LPCWSTR], wintypes.UINT),
            (shell.Shell_NotifyIconW, [wintypes.DWORD,
                                     ctypes.POINTER(NotifyData)], wintypes.BOOL),
        )
        for function, arguments, result in declarations:
            function.argtypes = arguments
            function.restype = result
        instance = kernel.GetModuleHandleW(None)
        message_id = 0x8001
        taskbar_created = user.RegisterWindowMessageW("TaskbarCreated")
        icon = user.LoadImageW(None, str(self.icon_path), 1, 32, 32, 0x0010)
        if not icon:
            raise ctypes.WinError(ctypes.get_last_error())
        notify = NotifyData()
        notify.size = ctypes.sizeof(NotifyData)
        notify.id = 1
        notify.flags = 1 | 2 | 4
        notify.callback = message_id
        notify.icon = icon
        notify.tip = "Ритм · задачи и напоминания"

        def add_icon():
            """Повторно зарегистрировать значок после перезапуска Explorer."""
            notify.flags = 1 | 2 | 4
            return bool(shell.Shell_NotifyIconW(0, ctypes.byref(notify)))

        def menu():
            """Показать меню с двумя явными действиями."""
            popup = user.CreatePopupMenu()
            try:
                user.AppendMenuW(popup, 0, 1, "Открыть Ритм")
                user.AppendMenuW(popup, 0x0800, 0, None)
                user.AppendMenuW(popup, 0, 2, "Завершить Ритм")
                point = wintypes.POINT()
                user.GetCursorPos(ctypes.byref(point))
                user.SetForegroundWindow(self.hwnd)
                selected = user.TrackPopupMenu(
                    popup, 0x0100 | 0x0002, point.x, point.y, 0, self.hwnd, None
                )
                if selected == 1:
                    self.on_open()
                elif selected == 2:
                    self.on_quit()
            finally:
                user.DestroyMenu(popup)

        def procedure(hwnd, message, wparam, lparam):
            """Обработать сообщения, не пропуская исключения через ctypes."""
            try:
                if message == message_id:
                    event = lparam & 0xFFFF
                    if event in (0x0202, 0x0203, 0x0405):
                        self.on_open()
                    elif event in (0x0205, 0x007B):
                        menu()
                    return 0
                if message == taskbar_created:
                    add_icon()
                    return 0
                if message == 0x0113:
                    try:
                        title, body, sound = self.messages.get_nowait()
                    except queue.Empty:
                        return 0
                    notify.flags = 0x0010
                    notify.title = title[:63]
                    notify.info = body[:255]
                    notify.info_flags = 1 | (0 if sound else 0x0010)
                    if not shell.Shell_NotifyIconW(1, ctypes.byref(notify)):
                        LOGGER.warning("Windows отклонил системное уведомление")
                    return 0
                if message == 0x0010:
                    user.DestroyWindow(hwnd)
                    return 0
                if message == 0x0002:
                    user.PostQuitMessage(0)
                    return 0
            except Exception:
                LOGGER.exception("Ошибка обработки события Windows tray")
            return user.DefWindowProcW(hwnd, message, wparam, lparam)

        callback = callback_type(procedure)
        window_class = WindowClass()
        window_class.proc = callback
        window_class.instance = instance
        window_class.name = f"RitmTray_{os.getpid()}"
        if not user.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = user.CreateWindowExW(
            0, window_class.name, "Ритм", 0, 0, 0, 0, 0,
            None, None, instance, None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        notify.hwnd = self.hwnd
        try:
            if not add_icon():
                raise ctypes.WinError(ctypes.get_last_error())
            user.SetTimer(self.hwnd, 1, 1000, None)
            self.available = True
            self.ready.set()
            message = wintypes.MSG()
            while True:
                result = user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        finally:
            user.KillTimer(self.hwnd, 1)
            shell.Shell_NotifyIconW(2, ctypes.byref(notify))
            user.DestroyIcon(icon)
