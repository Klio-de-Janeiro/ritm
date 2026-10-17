"""Проверки преобразования пользовательского PNG и ресурсов значка."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from prepare_icons import prepare_icons
from ritm.windows_identity import install_shortcuts


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow нужен при сборке")
class IconTests(unittest.TestCase):
    """Проверять альфа-канал, размеры ICO и повторную замену изображения."""

    def test_png_builds_all_windows_sizes_and_web_icon(self):
        """Замена корневого PNG меняет оба вида ресурсов."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for color in ("#35dcf7", "#a08bff"):
                image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
                image.paste(color, (100, 100, 412, 412))
                image.save(root / "Ritm.png")
                icon = prepare_icons(root)
                with Image.open(icon) as ico:
                    self.assertEqual(ico.ico.sizes(), {
                        (size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)
                    })
                with Image.open(root / "ritm/web/icon.png") as web:
                    self.assertEqual(web.size, (512, 512))
                    self.assertEqual(web.getpixel((0, 0))[3], 0)
                    self.assertEqual(web.getpixel((256, 256)),
                                     Image.new("RGBA", (1, 1), color).getpixel((0, 0)))

    def test_missing_or_invalid_source_is_explicit(self):
        """Сборка не подменяет пропавший/неподходящий источник старым ICO."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(RuntimeError, "Ritm.png"):
                prepare_icons(root)
            Image.new("RGB", (32, 32)).save(root / "Ritm.png")
            with self.assertRaisesRegex(RuntimeError, "256x256"):
                prepare_icons(root)

    @unittest.skipIf(os.name == "nt", "Изменяет ярлыки только по явной команде")
    def test_shortcut_installer_rejects_non_windows(self):
        """Linux не должен сообщать об установке ярлыка Windows."""
        with self.assertRaisesRegex(RuntimeError, "Windows"):
            install_shortcuts(Path("Ritm.exe"))
