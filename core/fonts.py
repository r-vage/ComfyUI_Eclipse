# Shared Eclipse font discovery; preserve the models/fonts convention.
import shutil
from pathlib import Path

import folder_paths


def ensure_font_dir():
    directory = Path(folder_paths.models_dir) / "fonts"
    directory.mkdir(parents=True, exist_ok=True)
    if not any(p.suffix.lower() in (".ttf", ".otf") for p in directory.iterdir()):
        for source in (Path(__file__).resolve().parent.parent / "fonts").iterdir():
            if source.suffix.lower() in (".ttf", ".otf"):
                target = directory / source.name
                if not target.exists():
                    shutil.copy2(source, target)
    return directory


def get_font_list():
    return sorted(
        p.name
        for p in ensure_font_dir().iterdir()
        if p.is_file() and p.suffix.lower() in (".ttf", ".otf")
    ) or ["(no fonts found)"]


def default_font():
    names = get_font_list()
    return next((n for n in names if n.lower() == "roboto-regular.ttf"), names[0])


def default_caption_font():
    return next(
        (name for name in get_font_list() if name.lower() == "quicksand-bold.ttf"),
        None,
    ) or default_font()


def get_font_path(name):
    if Path(name).name != name:
        raise ValueError("Select a font filename from models/fonts.")
    return str(Path(folder_paths.models_dir) / "fonts" / name)


def caption_font(name, size, text):
    from PIL import ImageFont, features

    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise RuntimeError(
            "Lyric captions require fonttools for glyph validation."
        ) from exc
    path = get_font_path(name)
    if not Path(path).is_file():
        raise ValueError(f"Missing font: {name}. Add a TTF/OTF font to models/fonts.")
    with TTFont(path) as font:
        cmap = font.getBestCmap() or {}
        import unicodedata

        missing = sorted(
            {
                ord(c)
                for c in text
                if not c.isspace()
                and unicodedata.category(c) != "Cf"
                and ord(c) not in cmap
            }
        )
    if missing:
        raise ValueError(
            "Font lacks required glyphs: " + ", ".join(f"U+{c:04X}" for c in missing)
        )
    if not features.check("raqm"):
        raise RuntimeError(
            "Lyric captions require Pillow with RAQM for multilingual shaping."
        )
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
