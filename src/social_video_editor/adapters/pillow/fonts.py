"""Loading fonts by name (cached)."""
from __future__ import annotations

from PIL import ImageFont

from ..system.fonts import find_font_file

_cache: dict = {}


def load_font(name: str, size: int):
    """Font by file name (no extension). 'Name:2' = face 2 of a .ttc. Falls back to Pillow's default."""
    key = (name, size)
    if key in _cache:
        return _cache[key]
    path, index = find_font_file(name)
    if path is None:
        return ImageFont.load_default()
    f = ImageFont.truetype(str(path), size, index=index)
    _cache[key] = f
    return f
