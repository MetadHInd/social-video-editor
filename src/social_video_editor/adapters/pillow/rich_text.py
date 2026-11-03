"""Text with color emoji (Apple Color Emoji on macOS, Noto Color Emoji on Linux) drawn with Pillow.

Units come from domain.text_units (a composed emoji is ONE unit). Line breaking is computed with
the full sentence, so nothing moves while it is being typed.
"""
from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from ...domain.text_units import is_emoji, units
from ..system.fonts import emoji_font


EMOJI_SIZES = (160, 109)   # bitmap emoji fonts only load at their strike size (Noto Color Emoji: 109)


@lru_cache(maxsize=1)
def _emoji_font():
    """(font, render size) of the installed color emoji font, or (None, 0) when there is none."""
    path = emoji_font()
    if path is not None:
        for s in EMOJI_SIZES:
            try:
                return ImageFont.truetype(str(path), s), s
            except OSError:
                continue
    return None, 0


@lru_cache(maxsize=512)
def _emoji_image(u: str, size: int) -> Image.Image:
    side = max(1, int(size * 1.0))
    f, s = _emoji_font()
    if f is None:                          # no emoji font installed: leave the space empty
        return Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas = int(s * 1.25)
    im = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((0, 0), u, font=f, embedded_color=True)
    bb = im.getbbox() or (0, 0, s, s)
    im = im.crop((0, 0, max(bb[2], s), max(bb[3], s)))
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
