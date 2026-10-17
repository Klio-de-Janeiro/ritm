"""Один Windows EXE со встроенными локальными ресурсами интерфейса."""

from pathlib import Path


root = Path(SPECPATH)
from prepare_icons import prepare_icons
prepare_icons(root)
analysis = Analysis(
    [str(root / "windows_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "ritm" / "web"), "ritm/web")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
python_archive = PYZ(analysis.pure)
executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="Ritm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(root / "ritm" / "web" / "icon.ico"),
)
