"""Подготовка значков Windows и веб-интерфейса из корневого Ritm.png."""

from pathlib import Path


def prepare_icons(root=None):
    """Преобразовать PNG в ICO с несколькими размерами без потери альфа."""
    from PIL import Image

    root = Path(root) if root else Path(__file__).resolve().parent
    source = root / "Ritm.png"
    if not source.is_file():
        raise RuntimeError("Place Ritm.png next to README.md before building.")
    with Image.open(source) as image:
        if image.format != "PNG" or min(image.size) < 256:
            raise RuntimeError("Ritm.png must be a PNG image of at least 256x256.")
        image = image.convert("RGBA")
        edge = max(image.size)
        square = Image.new("RGBA", (edge, edge))
        square.alpha_composite(image, ((edge - image.width) // 2,
                                       (edge - image.height) // 2))
        web = root / "ritm" / "web"
        web.mkdir(parents=True, exist_ok=True)
        square.resize((512, 512), Image.Resampling.LANCZOS).save(web / "icon.png")
        square.save(web / "icon.ico", format="ICO", sizes=[
            (size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)
        ])
    return web / "icon.ico"


if __name__ == "__main__":
    prepare_icons()
