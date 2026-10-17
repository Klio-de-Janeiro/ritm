"""Подготовить локальный MCP без изменения настроек ChatGPT пользователя."""

import json
import os
import subprocess
import sys
import venv
from pathlib import Path


def write_connection(root, python):
    """Сохранить готовые пути для формы STDIO и необязательный TOML-фрагмент."""
    command = str(python.resolve())
    script = str((root / "gpt_mcp.py").resolve())
    configuration = {
        "name": "Ritm", "transport": "stdio", "command": command,
        "args": [script],
    }
    (root / "GPT_MCP_CONNECTION.json").write_text(
        json.dumps(configuration, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    text = (
        "[mcp_servers.ritm]\n"
        f"command = {quote(command)}\n"
        f"args = [{quote(script)}]\n"
        "enabled = true\n"
    )
    (root / "GPT_MCP_CONFIG.toml").write_text(text, encoding="utf-8")
    print("\nChatGPT Desktop > Settings > MCP servers > Add server")
    print("Name: Ritm\nTransport: STDIO")
    print("Command: " + command)
    print("Arguments (one item): " + script)
    print("\nConnection details: " + str(root / "GPT_MCP_CONNECTION.json"))


def main():
    """Установить SDK в отдельное окружение и проверить список инструментов."""
    if not (3, 10) <= sys.version_info[:2] < (3, 15):
        print("Python 3.10-3.14 is required.", file=sys.stderr)
        return 1
    root = Path(__file__).resolve().parent
    directory = root / ".venv-gpt"
    python = directory / ("Scripts/python.exe" if os.name == "nt"
                          else "bin/python")
    try:
        if not python.is_file():
            venv.EnvBuilder(with_pip=True).create(directory)
        subprocess.run([
            str(python), "-m", "pip", "install", "-r",
            str(root / "requirements-gpt.txt"),
        ], check=True)
        subprocess.run([
            str(python), "-c", "from gpt_mcp import create_server; "
            "s=create_server(); print('MCP server initialized')",
        ], cwd=root, check=True)
        write_connection(root, python)
        check = subprocess.run([str(python), str(root / "gpt_mcp.py"),
                                "--check"], cwd=root)
        if check.returncode:
            print("\nSDK is ready. Start Ritm, then run this read-only check:")
            print(subprocess.list2cmdline([
                str(python), str(root / "gpt_mcp.py"), "--check",
            ]))
        else:
            print("\nRitm is reachable. No test task was created.")
        print("Save the MCP server in ChatGPT, select Restart, then test it.")
        return 0
    except (OSError, subprocess.CalledProcessError) as error:
        print("SETUP FAILED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
