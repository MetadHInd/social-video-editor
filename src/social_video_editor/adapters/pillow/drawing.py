"""Small image helpers for drawn shots (polaroid, collage)."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter


def soft_background(im: Image.Image, W: int, H: int, dark: float = 0.55, tint=(255, 214, 230)) -> Image.Image:
    """Background: the same image heavily blurred, darker and with a pastel tint."""
    f = im.resize((W // 4, H // 4)).filter(ImageFilter.GaussianBlur(10)).resize((W, H), Image.BILINEAR)
    layer = Image.new("RGB", (W, H), tint)
    f = Image.blend(f, layer, 0.18)
    return Image.eval(f, lambda v: int(v * dark)).convert("RGBA")


def rounded(im: Image.Image, radius: int) -> Image.Image:
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=radius, fill=255)
    out = im.convert("RGBA")
    out.putalpha(m)
    return out


def drop_shadow(im: Image.Image, radius: int = 18, opacity: int = 120, offset: int = 14) -> Image.Image:
    """The image with a soft shadow (on a slightly larger canvas)."""
    m = radius * 2 + offset
    canvas = Image.new("RGBA", (im.width + 2 * m, im.height + 2 * m), (0, 0, 0, 0))
    alpha = im.split()[-1].point(lambda a: a * opacity // 255)
    black = Image.new("RGBA", im.size, (0, 0, 0, 255))
    black.putalpha(alpha)
    canvas.alpha_composite(black, (m, m + offset))
    canvas = canvas.filter(ImageFilter.GaussianBlur(radius))
    canvas.alpha_composite(im, (m, m))
    return canvas


def center_crop(im: Image.Image, aspect: float, cy: float = 0.5) -> Image.Image:
    """Crops to the given aspect (width/height), centered (cy = vertical center 0-1)."""
    w, h = im.size
    if w / h > aspect:
        nw = int(h * aspect)
        x = (w - nw) // 2
        return im.crop((x, 0, x + nw, h))
    nh = int(w / aspect)
    y = int(min(max(h * cy - nh / 2, 0), h - nh))
    return im.crop((0, y, w, y + nh))


def ease_out(x: float) -> float:
    """Smooth 0→1 ease-out curve."""
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def to_rgb_bytes(im: Image.Image) -> bytes:
    return im.convert("RGB").tobytes()
