"""Text with color emoji (Apple Color Emoji) drawn with Pillow.

Units come from domain.text_units (a composed emoji is ONE unit). Line breaking is computed with
the full sentence, so nothing moves while it is being typed.
"""
from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from ...domain.text_units import is_emoji, units
from ..system.fonts import EMOJI_FONT


@lru_cache(maxsize=512)
def _emoji_image(u: str, size: int) -> Image.Image:
    f = ImageFont.truetype(str(EMOJI_FONT), 160)
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((0, 0), u, font=f, embedded_color=True)
    bb = im.getbbox() or (0, 0, 160, 160)
    im = im.crop((0, 0, max(bb[2], 160), max(bb[3], 160)))
    side = int(size * 1.0)
    return im.resize((side, int(im.height * side / im.width)), Image.LANCZOS)


def unit_width(u: str, font, size: int) -> float:
    return size * 1.08 if is_emoji(u) else font.getlength(u)


def width(us: list[str], font, size: int) -> float:
    return sum(unit_width(u, font, size) for u in us)


def wrap(text: str, font, size: int, max_px: float) -> list[list[str]]:
    """Splits into lines (by words). Returns lists of units; spaces inside a line are kept."""
    lines, current = [], []
    for word in text.split(" "):
        uw = units(word)
        trial = current + ([" "] if current else []) + uw
        if width(trial, font, size) <= max_px or not current:
            current = trial
        else:
            lines.append(current)
            current = uw
    if current:
        lines.append(current)
    return lines


def draw(img: Image.Image, x: float, y: float, us: list[str], font, size: int, color, stroke: int = 0,
         stroke_color=(0, 0, 0, 255)) -> float:
    """Draws a line of units from (x, y = top). Returns the final x."""
    d = ImageDraw.Draw(img)
    asc, _ = font.getmetrics()
    buf = ""
    x0 = x

    def flush():
        nonlocal buf, x0
        if buf:
            kw = {"stroke_width": stroke, "stroke_fill": stroke_color} if stroke else {}
            d.text((x0, y), buf, font=font, fill=color, **kw)
            x0 += font.getlength(buf)
            buf = ""

    for u in us:
        if is_emoji(u):
            flush()
            e = _emoji_image(u, size)
            img.alpha_composite(e, (int(x0 + size * 0.04), int(y + asc - e.height * 0.86)))
            x0 += size * 1.08
        else:
            buf += u
    flush()
    return x0
