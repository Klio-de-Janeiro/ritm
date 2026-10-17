"""Проверки миграции настроек темы, их валидации и хранения."""

import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ritm.store import Store, ValidationError
from ritm.config import COLOR_SETTINGS


class ThemeTests(unittest.TestCase):
    """Проверить оформление на существующей базе без потери задач."""

    def setUp(self):
        """Подготовить независимую базу для каждого сценария."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "theme.sqlite3"
        self.store = Store(self.path)

    def test_old_profile_gets_defaults_and_keeps_tasks(self):
        """Добавление настроек к старой версии не трогает историю."""
        self.store.create_task({"title": "Сохранить задачу"})
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "DELETE FROM settings WHERE key IN "
                "('theme','accent_color','neon_intensity')"
            )
            connection.commit()
        migrated = Store(self.path)
        self.assertEqual(migrated.settings()["theme"], "neon")
        self.assertEqual(migrated.settings()["accent_color"], "#35dcf7")
        self.assertEqual(len(migrated.list_tasks()), 1)

    def test_theme_persists_after_restart_and_in_export(self):
        """Выбор темы и пользовательского акцента сохраняется в SQLite."""
        self.store.update_settings({
            "theme": "light", "accent_color": "#A08BFF",
            "neon_intensity": 0,
        })
        restored = Store(self.path).settings()
        self.assertEqual(restored["theme"], "light")
        self.assertEqual(restored["accent_color"], "#a08bff")
        self.assertEqual(restored["neon_intensity"], 0)
        self.assertEqual(self.store.export()["settings"]["theme"], "light")

    def test_invalid_theme_settings_do_not_partially_save(self):
        """Неправильное оформление отклоняется до записи остальных полей."""
        original = self.store.settings()
        invalid = [
            {"theme": "unknown"}, {"theme": ["light"]},
            {"accent_color": "red"}, {"accent_color": "#123"},
            {"accent_color": "#ffffff; color:red"},
            {"accent_color": ["#ffffff"]},
            {"neon_intensity": True}, {"neon_intensity": "65"},
            {"neon_intensity": -1}, {"neon_intensity": 101},
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.store.update_settings(dict(display_name="Другое", **fields))
            self.assertEqual(self.store.settings(), original)

    def test_custom_palette_and_every_color_survive_restart(self):
        """Гамма, поверхности и эффекты сохраняются целиком."""
        values = {
            "palette_enabled": True, "palette_hue": 270,
            "palette_saturation": 65, "background_brightness": 10,
            "border_radius": 24, "borders": False, "gradients": False,
            "glow_enabled": False,
            **{key: "#AABBCC" for key in COLOR_SETTINGS},
        }
        self.store.update_settings(values)
        restored = Store(self.path).settings()
        for key, value in values.items():
            self.assertEqual(restored[key], value.lower() if isinstance(value, str) else value)
        self.store.update_settings({key: None for key in COLOR_SETTINGS})
        for key in COLOR_SETTINGS:
            self.assertIsNone(Store(self.path).settings()[key])

    def test_extended_appearance_validation_is_atomic(self):
        """Некорректный цвет/ползунок не меняет ранее сохранённую тему."""
        original = self.store.settings()
        for values in (
            {"palette_hue": 361}, {"palette_saturation": -1},
            {"background_brightness": True}, {"border_radius": 25},
            {"glow_enabled": "false"}, {"borders": 0},
            {"panel_color": "transparent"}, {"text_color": ["#ffffff"]},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.store.update_settings(dict(display_name="Другое", **values))
            self.assertEqual(self.store.settings(), original)

    @unittest.skipUnless(shutil.which("node"), "Node.js необязателен для сборки")
    def test_theme_engine_cache_and_color_contrast(self):
        """Выполнить тесты JS-движка без установки browser-зависимостей."""
        script = Path(__file__).with_name("theme_engine.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), str(script)], capture_output=True,
            text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js необязателен для сборки")
    def test_ui_editing_and_settings_handlers(self):
        """Проверить обработчики форм на модели DOM без загрузок из сети."""
        script = Path(__file__).with_name("ui_flow.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), str(script)], capture_output=True,
            text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_mobile_theme_and_progress_handlers(self):
        result = subprocess.run([shutil.which("node"), str(Path(__file__).with_name("mobile_ui.test.cjs"))],
            capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_awards_catalog_and_personal_action_milestones(self):
        result = subprocess.run([shutil.which("node"), str(Path(__file__).with_name("awards.test.cjs"))],
            capture_output=True,text=True,encoding="utf-8",check=False)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == "__main__":
    unittest.main()
