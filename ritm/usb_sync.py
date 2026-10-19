"""Восстанавливать USB-туннель только для уже разрешённых ADB-устройств."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

from .platform import InstanceLock, running_instance


def adb_path():
    found = shutil.which("adb")
    if found:
        return found
    roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(str(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk"))
    for root in roots:
        if root:
            path = Path(root) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
            if path.is_file():
                return str(path)
    return None


def devices(output):
    return [parts[0] for line in output.splitlines() if len(parts := line.split()) >= 2
            and parts[1] == "device" and re.fullmatch(r"[A-Za-z0-9_.-]+", parts[0])
            and not parts[0].startswith("emulator-")]


def run(adb, *args):
    result = subprocess.run([adb, *args], capture_output=True, text=True, timeout=5,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise OSError("ADB недоступен")
    return result.stdout


def reconnect(adb, port):
    for serial in devices(run(adb, "devices")):
        try:
            if "package:" not in run(adb, "-s", serial, "shell", "pm", "path", "ru.klio.ritm"):
                continue
            mappings = [line.split()[-2:] for line in run(adb, "-s", serial, "reverse", "--list").splitlines()]
            source, target = "tcp:8769", "tcp:" + str(port)
            if [source, target] in mappings:
                continue
            # Не заменять постороннее сопоставление того же порта.
            if any(pair and pair[0] == source for pair in mappings):
                continue
            run(adb, "-s", serial, "reverse", "--no-rebind", source, target)
        except (OSError, subprocess.SubprocessError):
            continue  # Кабель мог быть отключён между командами.


def serve(directory, stop):
    lock_dir = directory / "usb-bridge"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(lock_dir)
    try:
        if not lock.acquire():
            return
        while not stop.is_set():
            try:
                config = json.loads((directory / "mobile-sync.json").read_text(encoding="utf-8"))
                port = config.get("port", 8769)
                adb = adb_path()
                if config.get("enabled") and type(port) is int and 1 <= port <= 65535 and adb and running_instance(directory):
                    reconnect(adb, port)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            stop.wait(8)
    finally:
        lock.close()


def start(directory, stop):
    thread = threading.Thread(target=serve, args=(directory, stop), name="ritm-usb", daemon=True)
    thread.start()
    return thread


def main():
    parser = argparse.ArgumentParser(description="Ритм: автоматический обмен через USB-отладку")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Ritm")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    directory = args.data_dir.expanduser().resolve()
    if args.install:
        if os.name != "nt" or not adb_path():
            parser.error("Нужны Windows и установленные Android SDK Platform Tools (adb).")
        import winreg
        python = Path(sys.executable).with_name("pythonw.exe")
        if not python.is_file():
            parser.error("Не найден pythonw.exe для фонового запуска.")
        command = [str(python), str(Path(__file__).resolve().parents[1] / "USB_SYNC.py"), "--data-dir", str(directory)]
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.SetValueEx(key, "RitmUsbBridge", 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
        print("Автоматическое USB-подключение Ритма включено.")
    else:
        serve(directory, threading.Event())


if __name__ == "__main__":
    main()
