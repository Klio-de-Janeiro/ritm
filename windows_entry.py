"""Точка входа EXE без повторного запуска себя как Python-интерпретатора."""

import ctypes
import logging
import os

from ritm.app import main
from ritm.config import data_directory


def run():
    """Запустить приложение; показать ошибку даже без окна консоли."""
    try:
        result = main()
    except Exception:
        directory = data_directory()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        logging.basicConfig(
            filename=directory / "ritm.log", encoding="utf-8",
            level=logging.ERROR,
        )
        logging.exception("Ошибка точки входа EXE")
        result = 1
    if result and os.name == "nt":
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.MessageBoxW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_uint,
        ]
        user.MessageBoxW.restype = ctypes.c_int
        message = (
            "Не удалось запустить Ритм.\n"
            "Проверь, не занят ли порт 8768.\n\nЖурнал ошибок:\n"
            + str(data_directory() / "ritm.log")
        )
        user.MessageBoxW(None, message, "Ритм · ошибка запуска", 0x10)
    return result


if __name__ == "__main__":
    raise SystemExit(run())
