"""PhotoRenderer: a still photo → a segment like the other shots (raw RGB frames piped to FFmpeg).

1. The photo is opened with Pillow, the EXIF orientation applied (ImageOps.exif_transpose), and —
   with photo.auto_tone > 0 — its exposure gently fixed (auto_tone: a lightness curve in CIE Lab that
   lifts dark / backlit photos, a little local contrast; well-exposed photos are left untouched).
2. A canvas (1 + motion amount) × the output size is built ONCE with Lanczos: the photo covering it
   (centered on focus_x / focus_y), or — when the aspect ratios differ a lot (framing.mode auto, same
   rule as video) — the whole photo over a blurred, darker copy of itself. Optional gentle warmth.
3. Every frame is an affine resample of that canvas (OpenCV warpAffine, bicubic) at float positions,
   so the Ken Burns zoom / pan moves by fractions of a pixel: no integer-rounding jitter. The canvas is
   at least as large as the visible window, so frames are never enlarged from the canvas.
4. Optional vignette (fixed to the frame), then FfmpegEncoder.encode_frames: same codec, pixel format,
   fps and silent 48 kHz stereo track as polaroid / collage segments, so they join with the rest
   (quality render.photo_crf, default 16: a still photo compresses well).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ...domain.photos import (BRIGHT_LEVEL, MOTION_AMOUNT, SHADOW_LEVEL, auto_tone_lift, auto_tone_setting,
                              ken_burns, local_contrast_share, photo_crf, shadow_recovery, shadow_weight,
                              tone_curve)
from .encoder import FfmpegEncoder
from .filters import framing_mode
from .probe import is_image, scan_images

BLUR_DARKEN = 0.92            # the blurred background is a bit darker than the photo
WARMTH_GAINS = (0.10, 0.03, -0.10)   # R, G, B gain per unit of color.warmth
VIGNETTE_DEPTH = 0.45         # corner darkening at color.vignette = 1
TONE_MEASURE_SIDE = 512       # auto_tone measures the lightness on a copy this large (long side)
TONE_CLAHE_CLIP = 1.5         # low clip limit: gentle local contrast, never an "HDR" look
TONE_CLAHE_TILES = (8, 8)
TONE_CHROMA_FOLLOW = 0.5      # lifted pixels keep this share of their relative colorfulness…
TONE_CHROMA_MAX = 1.2         # …but their chroma never grows more than this (no color shifts)
ILLUM_SIDE = 160              # backlit photos: the illumination map is computed this large (long side)…
ILLUM_SIGMA_SPACE = 0.06      # …with an edge-aware (bilateral) blur this wide (share of the long side)
ILLUM_SIGMA_COLOR = 0.15      # lightness difference the blur does not cross (window edges: no halos)
ILLUM_DARK = (0.25, 0.65)     # illumination at which the local lift is full / gone


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def photo_color(cfg: dict, shot: dict) -> tuple[float, float]:
    """(warmth, vignette), each 0–1: settings `color` with the shot's `color` overrides.
    color.vignette: true = 0.5 (the video vignette setting is a boolean)."""
    c = {**(cfg.get("color") or {}), **(shot.get("color") or {})}

    def unit(v) -> float:
        if v is True:
            return 0.5
        try:
            return min(max(float(v or 0), 0.0), 1.0)
        except (TypeError, ValueError):
            return 0.0
    return unit(c.get("warmth")), unit(c.get("vignette"))


def motion_amount(cfg: dict, shot: dict) -> float:
    return float(shot.get("motion_amount", (cfg.get("photo") or {}).get("motion_amount", MOTION_AMOUNT)))


def lightness_stats(rgb: np.ndarray) -> dict:
    """CIE L* / 100 statistics of an RGB uint8 image, measured on a small copy: lower quartile, median,
    share of deep shadow (L* < 28) and share of bright pixels (L* > 85)."""
    L = _small_lightness(rgb)
    p25, p50 = np.percentile(L, [25, 50])
    return {"p25": float(p25), "p50": float(p50), "shadow": float((L < SHADOW_LEVEL).mean()),
            "bright": float((L > BRIGHT_LEVEL).mean())}


def lightness_quartiles(rgb: np.ndarray) -> tuple[float, float]:
    """(lower quartile, median) of the CIE L* / 100 of an RGB uint8 image (measured on a small copy)."""
    st = lightness_stats(rgb)
    return st["p25"], st["p50"]


def _small_lightness(rgb: np.ndarray) -> np.ndarray:
    import cv2

    h, w = rgb.shape[:2]
    scale = TONE_MEASURE_SIDE / max(h, w)
    if scale < 1:
        rgb = cv2.resize(rgb, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32) / 255


def illumination_map(L: np.ndarray) -> np.ndarray:
    """Smooth, edge-aware illumination of a lightness image (0–1, same size): bilateral blur of a small
    copy (it does not cross strong lightness edges such as a window frame), upscaled bilinearly."""
    import cv2

    h, w = L.shape
    scale = min(1.0, ILLUM_SIDE / max(h, w))
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    small = cv2.resize(L, (sw, sh), interpolation=cv2.INTER_AREA) if scale < 1 else L.copy()
    sigma = max(2.0, ILLUM_SIGMA_SPACE * max(sw, sh))
    for _ in range(2):                    # two passes: wide and still edge-aware
        small = cv2.bilateralFilter(small.astype(np.float32), -1, ILLUM_SIGMA_COLOR, sigma)
    small = cv2.GaussianBlur(small, (0, 0), 1.0)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR) if scale < 1 else small


def recover_shadows(L: np.ndarray, amount: float, lifted: np.ndarray | None = None) -> np.ndarray:
    """Backlit photos: lifts the shadows LOCALLY by up to `amount` (L* / 100). The lift follows a
    smooth illumination map (dark areas get it, bright windows / sky do not) and the pixel's own
    lightness through shadow_weight (blacks and highlights stay put); it is computed from a lightly
    denoised lightness, so the added offset is smooth and the noise is not amplified.
    `lifted` (the globally lifted lightness, if any): each pixel keeps the larger of the two lifts,
    they never add up."""
    import cv2

    illum = illumination_map(L)
    lo, hi = ILLUM_DARK
    t = np.clip((hi - illum) / (hi - lo), 0, 1)
    dark = t * t * (3 - 2 * t)
    base = cv2.GaussianBlur(L, (0, 0), 1.2)
    local = np.clip(L + amount * dark * shadow_weight(np.clip(base, 0, 1)), 0, 1)
    return local if lifted is None else np.maximum(lifted, local)


def auto_tone(img, strength: float):
    """Gentle automatic exposure fix of a Pillow RGB image (photo.auto_tone, 0–1): returns a new image,
    or the same one when nothing needs fixing (strength 0, or a photo already well exposed).

    Only the lightness changes (CIE Lab L*, tone_curve: blacks and whites stay where they are), plus a
    little CLAHE local contrast for lifted photos; backlit photos (deep shadows next to a bright window
    or sky) also get their shadows lifted locally (recover_shadows); chroma follows the lift partially so lifted colors
    keep their saturation without hue shifts. Deterministic.
    """
    if strength <= 0:
        return img
    import cv2
    from PIL import Image

    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    st = lightness_stats(arr)
    lift = auto_tone_lift(st["p25"], st["p50"], strength)
    recovery = shadow_recovery(st["shadow"], st["bright"], strength)
    if lift <= 0 and recovery <= 0:
        return img
    lab = cv2.cvtColor(arr.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)        # L 0–100, a / b ±127
    L = lab[..., 0] / 100
    L2 = tone_curve(L, lift)
    if recovery > 0:
        L2 = recover_shadows(L, recovery, L2)
    share = local_contrast_share(lift, strength)
    if share > 0:
        L8 = np.clip(L2 * 255 + 0.5, 0, 255).astype(np.uint8)
        clahe = cv2.createCLAHE(clipLimit=TONE_CLAHE_CLIP, tileGridSize=TONE_CLAHE_TILES)
        delta = (clahe.apply(L8).astype(np.float32) - L8.astype(np.float32)) / 255
        L2 = L2 + share * delta
    L2 = np.clip(L2, 0, 1)
    chroma = np.clip(1 + TONE_CHROMA_FOLLOW * ((L2 + 0.02) / (L + 0.02) - 1), 1.0, TONE_CHROMA_MAX)
    lab[..., 0] = L2 * 100
    lab[..., 1] *= chroma
    lab[..., 2] *= chroma
    rgb = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    return Image.fromarray(np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8), "RGB")


def load_photo(path: Path, min_side: int):
    """RGB Pillow image with the EXIF orientation applied; large JPEGs are decoded at a reduced scale
    that still has at least `min_side` pixels on both sides."""
    from PIL import Image, ImageOps

    im = Image.open(path)
    try:
        im.draft("RGB", (min_side, min_side))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            bg = Image.new("RGB", im.size, (0, 0, 0))
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").getchannel("A"))
            return bg
        return im.convert("RGB")
    finally:
        im.close()


def build_canvas(img, Wc: int, Hc: int, mode: str, focus: tuple[float, float], warmth: float) -> np.ndarray:
    """The photo framed on a Wc×Hc canvas (uint8 RGB): cover (crop), blur (blurred fill) or fit (black)."""
    from PIL import Image, ImageFilter

    w, h = img.size
    if mode in ("blur", "fit"):
        fit = min(Wc / w, Hc / h)
        fw, fh = max(1, round(w * fit)), max(1, round(h * fit))
        fg = img.resize((fw, fh), Image.LANCZOS)
        if mode == "blur":
            cov = max(Wc / w, Hc / h)
            small = img.resize((max(1, round(w * cov / 8)), max(1, round(h * cov / 8))), Image.BILINEAR)
            small = small.filter(ImageFilter.GaussianBlur(radius=max(2.0, min(small.size) / 40)))
            bg = small.resize((max(Wc, round(w * cov)), max(Hc, round(h * cov))), Image.BICUBIC)
            left, top = (bg.width - Wc) // 2, (bg.height - Hc) // 2
            bg = bg.crop((left, top, left + Wc, top + Hc)).point(lambda v: int(v * BLUR_DARKEN))
        else:
            bg = Image.new("RGB", (Wc, Hc), (0, 0, 0))
        bg.paste(fg, ((Wc - fw) // 2, (Hc - fh) // 2))
        canvas = bg
    else:
        cov = max(Wc / w, Hc / h)
        cw, ch = max(Wc, round(w * cov)), max(Hc, round(h * cov))
        left = round((cw - Wc) * min(max(focus[0], 0.0), 1.0))
        top = round((ch - Hc) * min(max(focus[1], 0.0), 1.0))
        # resize only the visible part (reducing_gap keeps big photos fast)
        scale_x, scale_y = w / cw, h / ch
        box = (left * scale_x, top * scale_y, (left + Wc) * scale_x, (top + Hc) * scale_y)
        canvas = img.resize((Wc, Hc), Image.LANCZOS, box=box, reducing_gap=3.0)
    arr = np.asarray(canvas, dtype=np.uint8)
    if warmth > 0:
        gains = np.array([1 + g * warmth for g in WARMTH_GAINS], np.float32)
        arr = np.clip(arr.astype(np.float32) * gains, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(arr)


def vignette_mask(W: int, H: int, strength: float) -> np.ndarray | None:
    """uint8 W×H×3 multiplier (255 = untouched): smooth darkening towards the corners."""
    if strength <= 0:
        return None
    y, x = np.ogrid[-1:1:complex(0, H), -1:1:complex(0, W)]
    d = np.sqrt(x * x + y * y) / math.sqrt(2)                    # 0 center … 1 corner
    m = 1 - VIGNETTE_DEPTH * strength * np.clip((d - 0.35) / 0.65, 0, 1) ** 2
    return np.repeat((m * 255).astype(np.uint8)[:, :, None], 3, axis=2)


def ken_burns_frames(canvas: np.ndarray, W: int, H: int, n: int, motion: str, amount: float,
                     focus: tuple[float, float], vignette: np.ndarray | None = None):
    """n raw RGB frames (bytes) of the canvas seen through the moving window."""
    import cv2

    Hc, Wc = canvas.shape[:2]
    for k in range(n):
        z, cx, cy = ken_burns(motion, k / max(n - 1, 1), amount, focus)
        sx, sy = Wc / (z * W), Hc / (z * H)                       # canvas pixels per output pixel (≥ 1)
        x0, y0 = cx * Wc - W * sx / 2, cy * Hc - H * sy / 2
        M = np.array([[sx, 0, x0 + 0.5 * sx - 0.5], [0, sy, y0 + 0.5 * sy - 0.5]], np.float64)
        frame = cv2.warpAffine(canvas, M, (W, H), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_REPLICATE)
        if vignette is not None:
            frame = cv2.multiply(frame, vignette, scale=1 / 255)
        yield frame.tobytes()


class FfmpegPhotoEncoder:
    def __init__(self, encoder: FfmpegEncoder | None = None):
        self.encoder = encoder or FfmpegEncoder()

    def is_image(self, path: str | Path) -> bool:
        return is_image(path)

    def scan(self, folder: str | Path) -> list[str]:
        return scan_images(folder)

    def encode_photo(self, image: Path, shot: dict, cfg: dict, W: int, H: int, fps: int, out: Path,
                     draft: bool = False) -> Path:
        n = max(1, int(round(float(shot["dur"]) * fps)))
        amount = min(max(motion_amount(cfg, shot), 0.0), 0.25)
        motion = shot.get("motion") or "none"
        Wc, Hc = _even(W * (1 + amount) + 2), _even(H * (1 + amount) + 2)
        img = load_photo(Path(image), max(Wc, Hc))
        img = auto_tone(img, auto_tone_setting(cfg, shot))        # before framing: the blurred fill matches
        framing = {**(cfg.get("framing") or {}), "mode": shot.get("framing") or (cfg.get("framing") or {}).get("mode", "auto")}
        mode = framing_mode({"width": img.width, "height": img.height}, W, H, {"framing": framing})
        focus = (float(shot.get("focus_x", framing.get("focus_x", 0.5))),
                 float(shot.get("focus_y", framing.get("focus_y", 0.5))))
        warmth, vignette = photo_color(cfg, shot)
        canvas = build_canvas(img, Wc, Hc, mode, focus, warmth)
        img.close()
        # the crop already used the focus: the motion is centered on the canvas
        frames = ken_burns_frames(canvas, W, H, n, motion, amount, (0.5, 0.5), vignette_mask(W, H, vignette))
        return self.encoder.encode_frames(frames, n, W, H, fps, Path(out), crf=photo_crf(cfg), draft=draft)
