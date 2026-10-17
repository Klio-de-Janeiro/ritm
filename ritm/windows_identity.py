"""Единые значки и AppUserModelID для окна Chromium и ярлыков Windows."""

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


APP_ID = "Klio.Ritm.Desktop"
TOAST_CLSID = "34c236b3-acb8-51d8-87ba-3802d16c5b36"
LOGGER = logging.getLogger(__name__)


def _guid(text):
    """Получить COM GUID без зависимости от Windows-пакетов."""
    return (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)


def _method(pointer, index, result, *arguments):
    """Получить метод COM-интерфейса по стабильному индексу vtable."""
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[0][index])


def _check(result):
    """Преобразовать неуспешный HRESULT в исключение."""
    if result < 0:
        raise OSError(f"Windows HRESULT 0x{result & 0xffffffff:08x}")


def _query(pointer, iid):
    """Получить дополнительный интерфейс с собственным счётчиком ссылок."""
    result = ctypes.c_void_p()
    _check(_method(pointer, 0, ctypes.c_long, ctypes.c_void_p,
                   ctypes.c_void_p)(pointer, ctypes.byref(_guid(iid)),
                                   ctypes.byref(result)))
    return result


def _release(pointer):
    """Освободить только принадлежащую вызову ссылку COM."""
    if pointer:
        _method(pointer, 2, ctypes.c_ulong)(pointer)


def _set_property(store, number, text, *, guid=False):
    """Задать строковую AppUserModel property с корректным PROPVARIANT x64."""
    class PropertyKey(ctypes.Structure):
        """GUID и DWORD PROPERTYKEY Windows."""
        _fields_ = [("fmtid", ctypes.c_byte * 16), ("pid", ctypes.c_uint32)]

    class ValueUnion(ctypes.Union):
        """Максимальный размер PROPVARIANT: counted array с указателем."""
        class Counted(ctypes.Structure):
            """Поле CA с выравниванием указателя Windows."""
            _fields_ = [("count", ctypes.c_uint32), ("pointer", ctypes.c_void_p)]
        _fields_ = [("text", ctypes.c_wchar_p), ("pointer", ctypes.c_void_p), ("counted", Counted)]

    class Variant(ctypes.Structure):
        """VT_LPWSTR с четырьмя начальными WORD."""
        _fields_ = [("vt", ctypes.c_uint16), ("r1", ctypes.c_uint16),
                    ("r2", ctypes.c_uint16), ("r3", ctypes.c_uint16),
                    ("value", ValueUnion)]

    key = PropertyKey(_guid("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), number)
    variant = Variant()
    if guid:
        guid_value = _guid(text)
        variant.vt = 72  # VT_CLSID, not a string containing a GUID.
        variant.value.pointer = ctypes.addressof(guid_value)
    else:
        variant.vt = 31
        variant.value.text = text
    _check(_method(store, 6, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
        store, ctypes.byref(key), ctypes.byref(variant)
    ))


def install_shortcuts(executable, *, desktop=True, arguments="", toast=False):
    """Создать ярлыки текущего пользователя, без прав администратора."""
    if os.name != "nt":
        raise RuntimeError("Shortcuts can be installed on Windows only.")
    executable = Path(executable).resolve()
    if not executable.is_file():
        raise RuntimeError("Build dist\\Ritm.exe first.")
    ole = ctypes.OleDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_uint32, ctypes.c_void_p,
                                    ctypes.c_void_p]
    ole.CoCreateInstance.restype = ctypes.c_long
    _check(ole.CoInitializeEx(None, 2))
    shell = ctypes.OleDLL("shell32")
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                          ctypes.c_void_p, ctypes.c_void_p]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    paths = []
    try:
        folders = ["a77f5d77-2e2b-44c3-a6a2-aba601054a51"]
        if desktop:
            folders.append("b4bfcc3a-db2c-424c-b029-7fe99a87c641")
        for folder in folders:
            location = ctypes.c_void_p()
            _check(shell.SHGetKnownFolderPath(ctypes.byref(_guid(folder)),
                                             0, None, ctypes.byref(location)))
            try:
                directory = Path(ctypes.wstring_at(location))
            finally:
                ole.CoTaskMemFree(location)
            directory.mkdir(parents=True, exist_ok=True)
            link_path = directory / "Ритм.lnk"
            link = ctypes.c_void_p()
            _check(ole.CoCreateInstance(
                ctypes.byref(_guid("00021401-0000-0000-c000-000000000046")),
                None, 1,
                ctypes.byref(_guid("000214f9-0000-0000-c000-000000000046")),
                ctypes.byref(link),
            ))
            try:
                for index, value in ((20, str(executable)),
                                     (9, str(executable.parent)),
                                     (7, "Ритм — задачи и напоминания")):
                    _check(_method(link, index, ctypes.c_long,
                                   ctypes.c_wchar_p)(link, value))
                _check(_method(link, 17, ctypes.c_long, ctypes.c_wchar_p,
                               ctypes.c_int)(link, str(executable), 0))
                if arguments:
                    _check(_method(link, 11, ctypes.c_long, ctypes.c_wchar_p)(link, arguments))
                store = _query(link, "886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
                try:
                    _set_property(store, 5, APP_ID)
                    if toast:
                        _set_property(store, 26, TOAST_CLSID, guid=True)
                    _check(_method(store, 7, ctypes.c_long)(store))
                finally:
                    _release(store)
                persist = _query(link, "0000010b-0000-0000-c000-000000000046")
                try:
                    _check(_method(persist, 6, ctypes.c_long,
                                   ctypes.c_wchar_p, ctypes.c_int)(
                                       persist, str(link_path), 1))
                finally:
                    _release(persist)
                paths.append(link_path)
            finally:
                _release(link)
    finally:
        ole.CoUninitialize()
    return paths


class WindowIdentity(threading.Thread):
    """Обновлять значок только app-окон выделенного браузерного профиля."""

    def __init__(self, directory, stop_event):
        """Связать монитор с жизненным циклом приложения."""
        super().__init__(name="ritm-window-icon", daemon=True)
        self.directory = Path(directory)
        self.stop_event = stop_event
        self.handles = []
        self.verified_windows = set()
        self.exit_lock = threading.Lock()
        self.last_exit = 0.0

    def exit_fullscreen(self):
        """Дождаться отпускания Esc и передать F11 только своему fullscreen-окну."""
        if not self.exit_lock.acquire(blocking=False):
            return {"exited": False}
        try:
            if time.monotonic() - self.last_exit < 0.75:
                return {"exited": False}
            return self._exit_fullscreen()
        finally:
            self.exit_lock.release()

    def _profile_pids(self):
        """Проверить точный browser-profile, в том числе до первого опроса монитора."""
        script = (
            "$needle='--user-data-dir=' + $env:RITM_ICON_PROFILE; "
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -in @('msedge.exe','chrome.exe') -and "
            "$_.CommandLine -and ($_.CommandLine.Contains('\"'+$needle+'\"') "
            "-or $_.CommandLine.EndsWith($needle) "
            "-or $_.CommandLine.Contains($needle+' ')) "
            "} | ForEach-Object { $_.ProcessId }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=dict(os.environ, RITM_ICON_PROFILE=str((self.directory / "browser-profile").resolve())),
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise OSError("Не удалось проверить процесс окна Ритма")
        return {int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()}

    @staticmethod
    def _covers_monitor(user, hwnd):
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user.MonitorFromWindow.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        rect, info = wintypes.RECT(), MonitorInfo()
        info.size = ctypes.sizeof(info)
        user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            monitor = user.MonitorFromWindow(hwnd, 2)
            if not user.GetWindowRect(hwnd, ctypes.byref(rect)) or not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return False
            return all(abs(getattr(rect, edge) - getattr(info.monitor, edge)) <= 2
                       for edge in ("left", "top", "right", "bottom"))
        finally:
            if previous:
                user.SetThreadDpiAwarenessContext(previous)

    def _exit_fullscreen(self):
        if os.name != "nt":
            return {"exited": False}
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user.GetAsyncKeyState.restype = ctypes.c_short
        hwnd = user.GetForegroundWindow()
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not hwnd:
            return {"exited": False}
        # Обычное/максимизированное окно нельзя переключать в fullscreen по Esc.
        if user.GetWindowLongW(hwnd, -16) & 0x00C00000 or not self._covers_monitor(user, hwnd):
            return {"exited": False}
        if (hwnd, pid.value) not in self.verified_windows:
            name, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(512)
            user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user.GetClassNameW(hwnd, name, len(name))
            user.GetWindowTextW(hwnd, title, len(title))
            if name.value != "Chrome_WidgetWin_1" or not title.value.startswith("Ритм ·") or pid.value not in self._profile_pids():
                return {"exited": False}
        deadline = time.monotonic() + 0.8
        # Esc/Shift/Ctrl/Alt/Win меняют смысл F11, если ещё удерживаются.
        while any(user.GetAsyncKeyState(key) & 0x8000 for key in (0x1B, 0x10, 0x11, 0x12, 0x5B, 0x5C)):
            if (time.monotonic() >= deadline or self.stop_event.wait(0.01)
                    or user.GetForegroundWindow() != hwnd):
                return {"exited": False}

        class MouseInput(ctypes.Structure):
            """Размер объединения INPUT одинаков с Win32 на x86/x64."""
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

        class KeyInput(ctypes.Structure):
            """Пара клавиш виртуальной клавиатуры Windows."""
            _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("extra", ctypes.c_size_t)]

        class InputUnion(ctypes.Union):
            """Нативное выравнивание записи SendInput."""
            _fields_ = [("mouse", MouseInput), ("key", KeyInput)]

        class Input(ctypes.Structure):
            """Одна запись нажатия или отпускания F11."""
            _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]

        inputs = (Input * 2)()
        for item in inputs:
            item.type = 1
            item.value.key.vk = 0x7A  # VK_F11
        inputs[1].value.key.flags = 2  # KEYEVENTF_KEYUP
        user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        user.SendInput.restype = wintypes.UINT
        current_pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(current_pid))
        if (user.GetForegroundWindow() != hwnd or current_pid.value != pid.value
                or user.GetWindowLongW(hwnd, -16) & 0x00C00000
                or not self._covers_monitor(user, hwnd)):
            return {"exited": False}
        if user.SendInput(2, inputs, ctypes.sizeof(Input)) != 2:
            raise ctypes.WinError(ctypes.get_last_error())
        self.last_exit = time.monotonic()
        return {"exited": True}

    def run(self):
        """Следить за пересозданием окна без изменения других браузеров."""
        if os.name != "nt":
            return
        while not self.stop_event.is_set():
            try:
                self._monitor()
            except Exception:
                LOGGER.exception("Не удалось обновить значок окна Ритма; повторная попытка")
                self.stop_event.wait(3)

    def _monitor(self):
        """Найти PID по точному профилю и задать HWND icon/AppUserModelID."""
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        shell = ctypes.OleDLL("shell32")
        ole = ctypes.OleDLL("ole32")
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        ole.CoInitializeEx.restype = ctypes.c_long
        _check(ole.CoInitializeEx(None, 2))
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                           wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                 ctypes.POINTER(wintypes.DWORD)]
        user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                   wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                   wintypes.UINT]
        user.LoadImageW.restype = wintypes.HANDLE
        user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT,
                                            wintypes.WPARAM, wintypes.LPARAM,
                                            wintypes.UINT, wintypes.UINT,
                                            ctypes.POINTER(ctypes.c_size_t)]
        user.DestroyIcon.argtypes = [wintypes.HICON]
        shell.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND,
                                                       ctypes.c_void_p,
                                                       ctypes.c_void_p]
        shell.SHGetPropertyStoreForWindow.restype = ctypes.c_long
        icon_path = Path(__file__).parent / "web" / "icon.ico"
        icons = [user.LoadImageW(None, str(icon_path), 1, size, size, 0x0010)
                 for size in (16, 32)]
        self.handles = icons
        if not all(icons):
            raise ctypes.WinError(ctypes.get_last_error())
        identity = set()
        seen = set()
        try:
            while not self.stop_event.is_set():
                candidates = {}

                def visit(hwnd, _):
                    """Найти app-окна, не меняя их до проверки профиля."""
                    name = ctypes.create_unicode_buffer(256)
                    title = ctypes.create_unicode_buffer(512)
                    user.GetClassNameW(hwnd, name, len(name))
                    user.GetWindowTextW(hwnd, title, len(title))
                    if name.value == "Chrome_WidgetWin_1" and title.value.startswith("Ритм ·"):
                        pid = wintypes.DWORD()
                        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        candidates[hwnd] = pid.value
                    return True

                user.EnumWindows(callback_type(visit), 0)
                pending = {hwnd: pid for hwnd, pid in candidates.items()
                           if (hwnd, pid) not in seen}
                if pending:
                    try:
                        identity = self._profile_pids()
                    except (OSError, subprocess.TimeoutExpired):
                        LOGGER.warning("Browser profile check failed; retrying", exc_info=True)
                        self.stop_event.wait(1)
                        continue
                    for hwnd, pid in pending.items():
                        if pid not in identity:
                            continue
                        try:
                            self._apply(hwnd, shell, user, icons)
                            seen.add((hwnd, pid))
                        except OSError:
                            LOGGER.exception("Не удалось задать значок окна")
                seen.intersection_update(set(candidates.items()))
                self.verified_windows = {(hwnd, pid) for hwnd, pid in candidates.items()
                                         if pid in identity}
                self.stop_event.wait(3)
        finally:
            # HWND продолжает использовать HICON после завершения монитора;
            # их освобождает ОС при завершении процесса.
            ole.CoUninitialize()

    @staticmethod
    def _apply(hwnd, shell, user, icons):
        """Согласовать значок, relaunch target и группировку с ярлыком Пуска."""
        store = ctypes.c_void_p()
        _check(shell.SHGetPropertyStoreForWindow(
            hwnd, ctypes.byref(_guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")),
            ctypes.byref(store),
        ))
        try:
            if getattr(sys, "frozen", False):
                executable = str(Path(sys.executable).resolve())
                _set_property(store, 2, subprocess.list2cmdline([executable]))
                _set_property(store, 3, executable + ",0")
                _set_property(store, 4, "Ритм")
            _set_property(store, 5, APP_ID)
            _check(_method(store, 7, ctypes.c_long)(store))
        finally:
            _release(store)
        for kind, icon in enumerate(icons):
            result = ctypes.c_size_t()
            user.SendMessageTimeoutW(hwnd, 0x0080, kind, icon, 2, 250,
                                     ctypes.byref(result))
