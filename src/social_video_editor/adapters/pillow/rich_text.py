"""Text with color emoji (Apple Color Emoji on macOS, Noto Color Emoji on Linux, Segoe UI Emoji on
Windows) drawn with Pillow.

Units come from domain.text_units (a composed emoji is ONE unit). Line breaking is computed with
the full sentence, so nothing moves while it is being typed.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...domain.text_units import is_emoji, units
from ...log import log
from ..system.fonts import emoji_font


EMOJI_SIZES = (160, 109)   # bitmap emoji fonts only load at their strike size (Noto Color Emoji: 109)
COLOR_SPREAD = 24          # min. channel difference of at least one pixel for a glyph to count as "in color"


@lru_cache(maxsize=32)
def _emoji_font(size: int):
    """(font, render size) of the installed color emoji font, or (None, 0) when there is none.

    Scalable color fonts (Segoe UI Emoji's COLR glyphs) load at `size`; bitmap fonts (Noto Color Emoji,
    Apple Color Emoji) raise OSError at sizes they have no strike for, so they load at a strike size and
    the glyph is resized."""
    path = emoji_font()
    if path is None:
        return None, 0
    for s in (size, *EMOJI_SIZES):
        try:
            return ImageFont.truetype(str(path), s), s
        except OSError:
            continue
    return None, 0


def _render(u: str, f, s: int) -> Image.Image:
    canvas = int(s * 1.25)
    im = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((0, 0), u, font=f, embedded_color=True)
    return im


@lru_cache(maxsize=1)
def _emoji_color_ok() -> bool:
    """True when the emoji font draws in color here. FreeType builds without support for a font's color
    tables (e.g. COLR) draw grey outlines or nothing; then emoji are left out rather than drawn grey."""
    f, s = _emoji_font(EMOJI_SIZES[-1])
    if f is None:
        return False
    try:
        im = _render("\U0001F600", f, s)                        # 😀
        px = np.asarray(im, dtype=np.int16)
        rgb = px[px[..., 3] > 0][:, :3]                          # visible pixels
        ok = rgb.size > 0 and int((rgb.max(1) - rgb.min(1)).max()) >= COLOR_SPREAD
    except Exception:  # noqa: BLE001 - any failure of the font or of FreeType means "no color emoji"
        ok = False
    if not ok:
        path = emoji_font()
        log(f"Warning: the emoji font ({path.name if path else '?'}) does not draw in color here; "
            "emoji are left out.")
    return ok


@lru_cache(maxsize=512)
def _emoji_image(u: str, size: int) -> Image.Image:
    """The emoji `u` about `size` px wide; an empty square when it cannot be drawn in color (never raises)."""
    side = max(1, int(size * 1.0))
    empty = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    f, s = _emoji_font(side)
    if f is None or not _emoji_color_ok():          # no (color) emoji font: leave the space empty
        return empty
    try:
        im = _render(u, f, s)
        bb = im.getbbox() or (0, 0, s, s)
        im = im.crop((0, 0, max(bb[2], s), max(bb[3], s)))
        if im.size == (side, side):
            return im
        return im.resize((side, int(im.height * side / im.width)), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - one glyph the font cannot draw must not stop a render
        return empty


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
