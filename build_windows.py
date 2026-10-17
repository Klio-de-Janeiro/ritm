"""Изолированная сборка Windows EXE и проверка упакованного приложения."""

import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from ritm.server import WEB_FILES, load_web_assets


ROOT = Path(__file__).resolve().parent


def validate_environment():
    """Проверить платформу, Python и полноту исходного проекта."""
    if os.name != "nt":
        raise RuntimeError("Build the Windows EXE on Windows.")
    if not (3, 10) <= sys.version_info[:2] < (3, 15):
        raise RuntimeError("Use Python 3.10-3.14 for this build.")
    if struct.calcsize("P") != 8:
        raise RuntimeError("Use a 64-bit Python installation.")
    required = [
        ROOT / "windows_entry.py", ROOT / "Ritm.spec",
        ROOT / "ritm" / "app.py", ROOT / "requirements-build.txt",
        ROOT / "Ritm.png", ROOT / "prepare_icons.py",
    ]
    required.extend(ROOT / "ritm" / "web" / name for name in WEB_FILES)
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"Missing project file: {path}")


def run_step(command):
    """Выполнить этап и остановить сборку при ненулевом коде возврата."""
    subprocess.run([str(part) for part in command], cwd=ROOT, check=True)


def build_python():
    """Подготовить отдельное окружение сборки для выбранной версии Python."""
    tag = f"py{sys.version_info.major}{sys.version_info.minor}-64"
    directory = ROOT / ".venv-build" / tag
    executable = directory / "Scripts" / "python.exe"
    if not executable.is_file():
        venv.EnvBuilder(with_pip=True).create(directory)
    run_step([
        executable, "-c",
        "import struct,sys; "
        f"assert sys.version_info[:2] == {sys.version_info[:2]!r}; "
        "assert struct.calcsize('P') == 8",
    ])
    return executable


def request_json(opener, origin, token, path, payload=None):
    """Обратиться к изолированному API проверяемого EXE без системного proxy."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        origin + path, data=data,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with opener.open(request, timeout=3) as response:
        return json.load(response)


def stop_test_process(process):
    """Остановить только дерево созданного проверочного процесса."""
    if process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        process.wait(timeout=10)


def smoke_test(executable):
    """Проверить запуск EXE, HTML/CSS/JS, SQLite и остановку без GUI."""
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="ritm-build-check-") as folder:
        directory = Path(folder)
        process = subprocess.Popen([
            str(executable), "--background", "--no-tray", "--port", "0",
            "--data-dir", str(directory),
        ], cwd=executable.parent)
        origin = token = None
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Packaged application exited on start.")
                try:
                    runtime = json.loads(
                        (directory / "runtime.json").read_text("utf-8")
                    )
                    port = runtime["port"]
                    if type(port) is not int or not 1 <= port <= 65535:
                        raise ValueError("Invalid runtime port.")
                    origin = f"http://127.0.0.1:{port}"
                    token = (directory / "api.key").read_text().strip()
                    request_json(opener, origin, token, "/api/v1/state")
                    break
                except (OSError, ValueError, KeyError, URLError):
                    time.sleep(0.25)
            else:
                raise RuntimeError("Packaged API did not start in 90 seconds.")
            expected_assets = load_web_assets()
            for name in WEB_FILES:
                path = "/favicon.ico" if name == "icon.ico" else "/" + name
                with opener.open(origin + path, timeout=3) as response:
                    if (response.status != 200
                            or response.read() != expected_assets[name]):
                        raise RuntimeError("Mismatched packaged resource: " + name)
                    expected_type = {".css": "text/css",
                                     ".js": "text/javascript"}.get(
                                         Path(name).suffix
                                     )
                    if expected_type and response.headers.get_content_type() != expected_type:
                        raise RuntimeError("Wrong resource MIME type: " + name)
            task = request_json(
                opener, origin, token, "/api/v1/tasks",
                {"title": "EXE build verification"},
            )
            current = request_json(opener, origin, token, "/api/v1/state")
            if not any(row["task_id"] == task["id"]
                       for row in current["tasks"]):
                raise RuntimeError("Packaged SQLite verification failed.")
            request_json(opener, origin, token, "/api/v1/app/quit", {})
            if process.wait(timeout=15) != 0:
                raise RuntimeError("Packaged application did not stop cleanly.")
        except Exception:
            log = directory / "ritm.log"
            if log.exists():
                print(log.read_text("utf-8", errors="replace"), flush=True)
            raise
        finally:
            if process.poll() is None and origin and token:
                try:
                    request_json(opener, origin, token, "/api/v1/app/quit", {})
                    process.wait(timeout=5)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            stop_test_process(process)


def main():
    """Собрать один EXE; сообщать об успехе только после smoke-проверки."""
    try:
        validate_environment()
        print("[1/5] Preparing isolated build environment...", flush=True)
        python = build_python()
        print("[2/5] Installing build dependencies...", flush=True)
        run_step([
            python, "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "-r", ROOT / "requirements-build.txt",
        ])
        run_step([python, ROOT / "prepare_icons.py"])
        print("[3/5] Running application tests...", flush=True)
        run_step([python, "-m", "unittest", "discover", "-s", "tests", "-v"])
        print("[4/5] Building dist\\Ritm.exe...", flush=True)
        run_step([
            python, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", ROOT / "dist", "--workpath", ROOT / ".build",
            ROOT / "Ritm.spec",
        ])
        executable = ROOT / "dist" / "Ritm.exe"
        if not executable.is_file():
            raise RuntimeError("PyInstaller did not produce dist\\Ritm.exe.")
        print("[5/5] Checking packaged runtime (up to 90 seconds)...", flush=True)
        smoke_test(executable)
        print("\nBUILD OK: " + str(executable), flush=True)
        print("The resulting EXE does not require installed Python.")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("\nBUILD FAILED: " + str(error), file=sys.stderr)
        print("Check the error above. No success was confirmed.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
