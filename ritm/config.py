"""Пути приложения и безопасные локальные настройки."""

import os
import secrets
from pathlib import Path


DEFAULT_PORT = 8768
CATEGORIES = ("Работа", "Учёба", "Здоровье", "Личное")
HOUR_INTERVALS = {
    "hourly": 1, "every_3_hours": 3, "every_5_hours": 5,
    "every_8_hours": 8, "every_12_hours": 12,
}
RECURRENCES = ("once", *HOUR_INTERVALS, "daily", "every_3_days",
               "weekdays", "weekly", "monthly", "three_times_daily", "event")
PRIORITIES = ("low", "normal", "high")
THEMES = ("neon", "midnight", "light")
COLOR_SETTINGS = (
    "background_color", "panel_color", "sidebar_color", "field_color",
    "text_color", "muted_color", "border_color", "glow_color",
)
APPEARANCE_RANGES = {
    "neon_intensity": (0, 100), "palette_hue": (0, 360),
    "palette_saturation": (0, 100), "background_brightness": (0, 100),
    "border_radius": (0, 24),
}
DEFAULT_SETTINGS = {
    "display_name": "Клио",
    "notifications": True,
    "sound": True,
    "autostart": False,
    "theme": "neon",
    "accent_color": "#35dcf7",
    "neon_intensity": 65,
    "palette_enabled": False,
    "palette_hue": 190,
    "palette_saturation": 80,
    "background_brightness": 6,
    "glow_enabled": True,
    "gradients": True,
    "borders": True,
    "border_radius": 16,
    **{key: None for key in COLOR_SETTINGS},
}


def data_directory():
    """Вернуть каталог данных текущего пользователя."""
    override = os.environ.get("RITM_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / "Ritm"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "ritm"


def api_token(directory):
    """Создать или прочитать постоянный токен локального API."""
    path = directory / "api.key"
    try:
        descriptor = os.open(
            path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise ValueError("Повреждён api.key. Необходим токен от 32 символов.")
        return token
    token = secrets.token_urlsafe(32)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(token)
    return token
