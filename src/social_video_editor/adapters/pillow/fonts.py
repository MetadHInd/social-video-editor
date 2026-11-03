"""Loading fonts by name (cached)."""
from __future__ import annotations

from PIL import ImageFont

from ..system.fonts import find_font_file

_cache: dict = {}


def default_font(size: int):
    """Pillow's built-in font at `size` (scalable since Pillow 10.1; older versions only have a tiny
    bitmap font). Used when no font file is installed, so text keeps its intended size."""
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError, ImportError):     # Pillow < 10.1, or Pillow built without FreeType
        return ImageFont.load_default()


def load_font(name: str, size: int):
    """Font by file name (no extension). 'Name:2' = face 2 of a .ttc. Falls back to Pillow's default."""
    key = (name, size)
    if key in _cache:
        return _cache[key]
    path, index = find_font_file(name)
    if path is None:
        return default_font(size)          # not cached: a font installed later is picked up
    try:
        f = ImageFont.truetype(str(path), size, index=index)
    except OSError:                        # unreadable file or face index out of range
        return default_font(size)
    _cache[key] = f
    return f
