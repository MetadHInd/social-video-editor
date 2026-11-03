"""ImageUpscaler with Pillow only: Lanczos resize + a mild unsharp mask when enlarging (always available)."""
from __future__ import annotations

from pathlib import Path

JPEG_QUALITY = 95


def load_oriented(src: Path):
    """RGB Pillow image with the EXIF orientation applied (transparent areas become black)."""
    from PIL import Image, ImageOps

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (0, 0, 0))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            return bg
        return im.convert("RGB")


def resize_short(img, short_side: int):
    """Lanczos resize so the short side is `short_side` (aspect kept)."""
    from PIL import Image

    w, h = img.size
    f = short_side / min(w, h)
    size = (max(1, round(w * f)), max(1, round(h * f)))
    return img if size == img.size else img.resize(size, Image.LANCZOS, reducing_gap=3.0 if f < 1 else None)


def save_jpeg(img, out: Path) -> Path:
    """JPEG quality 95 written atomically (a half-written file never looks finished)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.jpg")
    img.save(tmp, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
    tmp.replace(out)
    return out


class LanczosUpscaler:
    def describe(self, settings: dict) -> str:
        return "Lanczos + unsharp mask (no AI model)"

    def enhance(self, src: Path, out: Path, short_side: int, upscale: bool, settings: dict) -> str:
        from PIL import ImageFilter

        img = resize_short(load_oriented(src), int(short_side))
        if upscale:
            img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
        save_jpeg(img, out)
        return "lanczos"
