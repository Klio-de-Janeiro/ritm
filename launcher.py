"""Запустить фоновый процесс и открыть приложение без окна консоли."""

import subprocess
import sys
import time
from pathlib import Path

from ritm.config import data_directory
from ritm.platform import open_window, running_instance


def main():
    """Повторно использовать процесс или запустить новый и дождаться API."""
    if sys.version_info < (3, 10):
        print("Ritm requires Python 3.10 or newer.")
        return 1
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = running_instance(directory)
    if existing:
        open_window(
            f"http://127.0.0.1:{existing['port']}/open/{existing['session']}",
            directory,
        )
        return 0
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if windowless.is_file():
        executable = windowless
    entry = Path(__file__).with_name("main.py")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with (directory / "launcher.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(executable), str(entry), "--background"],
            cwd=entry.parent, stdout=log, stderr=log,
            creationflags=flags, start_new_session=sys.platform != "win32",
        )
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        existing = running_instance(directory)
        if existing:
            open_window(
                f"http://127.0.0.1:{existing['port']}/open/{existing['session']}",
                directory,
            )
            return 0
        if process.poll() is not None:
            break
        time.sleep(0.2)
    print("Ritm could not start. See logs in: " + str(directory))
    print("If port 8768 is in use: python main.py --port 8769")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
