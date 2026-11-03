"""Still photos as source material (pure logic: no files, no images).

  - capture time from EXIF text (DateTimeOriginal, then DateTime, then the file's modification time)
  - chronological order (capture time, then file name / path to break ties)
  - gentle Ken Burns motions (zoom_in, zoom_out, pan_left, pan_right), assigned deterministically
  - the plan of a photo slideshow (one "photo" shot per photo)
  - how much a photo must be enlarged / reduced by `sve enhance`, and the name of its cached copy
  - the gentle automatic exposure fix of `photo.auto_tone` (tone curve math; the pixels are processed
    by the photo renderer)
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from pathlib import PurePath

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp")
PHOTO_SECONDS = 4.5            # default on-screen time of a photo (settings: photo.duration)
MOTION_AMOUNT = 0.06           # default maximum Ken Burns zoom (6 %; settings: photo.motion_amount)
MAX_MOTION_AMOUNT = 0.25       # hard limit (the renderer keeps this much headroom at most)
MOTIONS = ("zoom_in", "pan_right", "zoom_out", "pan_left")     # the rotation for landscape-friendly photos
ZOOM_MOTIONS = ("zoom_in", "zoom_out")                         # photos that need a blurred background
ALL_MOTIONS = (*MOTIONS, "none")
EASE = 0.35                    # share of smoothstep blended into the linear progress (soft start/end)
ZOOM_ONLY_RATIO = 1.8         # aspect ratios this different (portrait photo in a landscape video): zooms only
ENHANCE_HEADROOM = 1.25        # large photos are reduced to this × the output short side (Ken Burns room)
TAKEN_SOURCES = ("exif_original", "exif", "mtime")
PHOTO_CRF = 16                 # render.photo_crf: x264 quality of the cached photo segments


def is_image_name(path: str | PurePath) -> bool:
    return PurePath(str(path)).suffix.lower() in IMAGE_EXTENSIONS


# ── capture time and order ───────────────────────────────────────────────────

def parse_exif_datetime(text) -> str | None:
    """EXIF "YYYY:MM:DD HH:MM:SS" (also "YYYY-MM-DD…", with or without seconds) → ISO
    "YYYY-MM-DDTHH:MM:SS"; None for empty, zeroed ("0000:00:00 …") or invalid values."""
    if isinstance(text, bytes):
        text = text.decode("ascii", "ignore")
    s = str(text or "").strip().strip("\x00").strip()
    if not s:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(s[:19], fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def capture_time(date_original=None, date_time=None, mtime: float | None = None) -> tuple[str | None, str | None]:
    """(ISO capture time, where it came from): EXIF DateTimeOriginal (36867), else EXIF DateTime (306),
    else the file's modification time (local time, like EXIF)."""
    for value, origin in ((date_original, "exif_original"), (date_time, "exif")):
        iso = parse_exif_datetime(value)
        if iso:
            return iso, origin
    if mtime is not None:
        return datetime.fromtimestamp(float(mtime)).isoformat(timespec="seconds"), "mtime"
    return None, None


def displayed_size(width: int, height: int, orientation: int | None) -> tuple[int, int]:
    """Size once the EXIF orientation is applied (5–8 swap width and height)."""
    return (height, width) if int(orientation or 1) in (5, 6, 7, 8) else (width, height)


def chronological(photos: list[dict]) -> list[dict]:
    """Photos sorted by capture time ("taken", ISO text), then file name, then full path.
    Photos without a capture time go last (in name order)."""
    def key(p: dict):
        path = str(p.get("path", ""))
        return (p.get("taken") is None, p.get("taken") or "", PurePath(path).name.lower(), path)
    return sorted(photos, key=key)


# ── Ken Burns ────────────────────────────────────────────────────────────────

def needs_background(width: int, height: int, W: int, H: int, threshold: float = 1.3) -> bool:
    """Whether the photo's aspect ratio differs so much from the output's that it is shown whole over
    a blurred copy of itself (e.g. a portrait photo in a landscape video)."""
    r_src, r_out = width / max(height, 1), W / max(H, 1)
    return max(r_src, r_out) / min(r_src, r_out) > threshold


def assign_motions(photos: list[dict], W: int, H: int) -> list[str]:
    """One motion per photo, deterministic and alternating gently: zoom in, pan right, zoom out, pan left…
    Photos whose aspect is very different from the output's (ZOOM_ONLY_RATIO, e.g. a portrait photo in a
    landscape video, shown over a blurred background) only zoom (in / out alternately)."""
    out, k = [], 0
    for p in photos:
        w, h = int(p.get("width") or W), int(p.get("height") or H)
        if needs_background(w, h, W, H, ZOOM_ONLY_RATIO):
            out.append(ZOOM_MOTIONS[0] if not out or out[-1] != ZOOM_MOTIONS[0] else ZOOM_MOTIONS[1])
        else:
            m = MOTIONS[k % len(MOTIONS)]
            if out and m == out[-1]:
                k += 1
                m = MOTIONS[k % len(MOTIONS)]
            out.append(m)
        k += 1
    return out


def default_motion(index: int) -> str:
    return MOTIONS[index % len(MOTIONS)]


def eased(progress: float, ease: float = EASE) -> float:
    """Linear progress with a little smoothstep blended in: constant speed, soft start and end."""
    t = min(max(float(progress), 0.0), 1.0)
    return (1 - ease) * t + ease * t * t * (3 - 2 * t)


def ken_burns(motion: str, progress: float, amount: float = MOTION_AMOUNT,
              focus: tuple[float, float] = (0.5, 0.5)) -> tuple[float, float, float]:
    """(zoom ≥ 1, center x, center y) of the visible window at `progress` (0–1) of the shot.

    The center is relative to the canvas (0–1) and always keeps the window inside it.
    zoom_in: 1 → 1+amount; zoom_out: the reverse; pan_left / pan_right: zoomed 1+amount, the window
    travels across the whole free width (pan_right = the camera moves right).
    """
    a = min(max(float(amount), 0.0), MAX_MOTION_AMOUNT)
    p = eased(progress)
    if motion == "zoom_in":
        z = 1 + a * p
    elif motion == "zoom_out":
        z = 1 + a * (1 - p)
    elif motion in ("pan_left", "pan_right"):
        z = 1 + a
    else:
        z = 1.0
    half = 0.5 / z
    lo, hi = half, 1 - half

    def clamp(v: float) -> float:
        return min(max(v, lo), hi)

    cx, cy = clamp(float(focus[0])), clamp(float(focus[1]))
    if motion == "pan_right":
        cx = lo + (hi - lo) * p
    elif motion == "pan_left":
        cx = hi - (hi - lo) * p
    return z, cx, cy


# ── plan ─────────────────────────────────────────────────────────────────────

def photo_settings(cfg: dict) -> dict:
    p = cfg.get("photo") or {}
    return {"duration": float(p.get("duration", PHOTO_SECONDS)),
            "transition": p.get("transition", "fade"),
            "motion_amount": float(p.get("motion_amount", MOTION_AMOUNT))}


def photo_crf(cfg: dict) -> int:
    """render.photo_crf (default 16): quality of the cached photo segments (a still photo compresses
    well, so a higher number than the drawn shots' 12 keeps the cache small without visible loss)."""
    v = (cfg.get("render") or {}).get("photo_crf")
    return PHOTO_CRF if v is None else int(v)


def is_slideshow(cfg: dict, infos: list[dict]) -> bool:
    """A photo slideshow: the recipe says `slideshow: true`, or every source is a photo."""
    if cfg.get("slideshow"):
        return True
    return bool(infos) and all(i.get("image") for i in infos)


def photo_plan(photos: list[dict], cfg: dict) -> dict:
    """One "photo" shot per photo, in chronological order, `photo.duration` seconds each, entering with
    `photo.transition` (fade) and alternating gentle motions. Durations stay editable in plan.json.

    photos: media infos with "path", "width", "height", "taken" (and optionally "image": True).
    """
    s = photo_settings(cfg)
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    ordered = chronological([p for p in photos if p.get("image", True)])
    shots = []
    for i, (p, motion) in enumerate(zip(ordered, assign_motions(ordered, W, H))):
        shot = {"type": "photo", "source": str(p["path"]), "duration": round(s["duration"], 3),
                "motion": motion, "transition": "cut" if i == 0 else s["transition"]}
        if p.get("taken"):
            shot["note"] = str(p["taken"]).replace("T", " ")
        shots.append(shot)
    return {"note": f"Photo slideshow: {len(shots)} photos in chronological order", "shots": shots, "texts": []}


def resolve_photo_ref(ref, infos: list[dict]) -> str:
    """Path of the photo a shot's "source" refers to: a source index, a source id, a source path
    (as written in project.yaml / by the planner), or any other path (returned as given)."""
    if isinstance(ref, bool):
        raise ValueError(f"Invalid photo source: {ref!r}")
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        k = int(ref)
        if not 0 <= k < len(infos):
            raise ValueError(f"Photo source {k} does not exist (there are {len(infos)} sources)")
        return str(infos[k]["path"])
    ref = str(ref)
    for info in infos:
        if ref in (info.get("id"), str(info.get("path"))):
            return str(info["path"])
    return ref


# ── enhancement (sve enhance) ────────────────────────────────────────────────

def enhance_target(width: int, height: int, out_short: int,
                   headroom: float = ENHANCE_HEADROOM) -> tuple[str, int]:
    """What `sve enhance` does with a photo of this (displayed) size → (action, target short side).

    "upscale":   short side below the output's → model ×4, then Lanczos down to the output short side.
    "downscale": short side above headroom × the output's → Lanczos down to that (room for Ken Burns).
    "keep":      in between: the original is good as it is (nothing written).
    """
    short = min(int(width), int(height))
    if short < out_short:
        return "upscale", int(out_short)
    limit = int(math.ceil(out_short * headroom))
    if short > limit:
        return "downscale", limit
    return "keep", short


def enhanced_name(path: str, mtime_ns) -> str:
    """Stable name of the enhanced copy of a photo (hash of its resolved path and modification time)."""
    return hashlib.sha1(f"{path}|{mtime_ns}".encode()).hexdigest()[:20] + ".jpg"


# ── automatic exposure fix (photo.auto_tone) ────────────────────────────────
# Lightness values below are CIE L* / 100 (0 = black, 1 = white), measured on the photo.

TONE_TARGET_MID = 0.45         # a dark photo's median is lifted towards this…
TONE_TARGET_SHADOW = 0.30      # …and its lower quartile towards this (backlit subjects)
TONE_MAX_LIFT = 2.0            # hard limit of the curve strength (slope at black = 1 + lift ≤ 3)
TONE_LOCAL_CONTRAST = 0.4      # share of local contrast (CLAHE) blended in at full lift and strength
TONE_LOCAL_FULL_LIFT = 1.0     # lift from which the local contrast share is complete


def tone_curve(x, lift: float):
    """Shadow / midtone lift: f(x) = x + lift · x · (1 − x)², for L* / 100 values (floats or arrays).

    f(0) = 0 (blacks stay black: never washed out), f(1) = 1 with slope 1 (highlights keep their
    contrast and never clip), strongest at x = 1/3. Monotonic for lift ≤ 3 (TONE_MAX_LIFT is 2).
    """
    return x + lift * x * (1 - x) * (1 - x)


def _lift_for(value: float, target: float) -> float:
    """Curve strength that moves `value` to `target` (0 when it is already there or above)."""
    if value >= target:
        return 0.0
    v = max(float(value), 0.02)
    return (target - v) / (v * (1 - v) ** 2)


def auto_tone_lift(p25: float, p50: float, strength: float) -> float:
    """Strength of tone_curve for a photo whose lightness quartile / median are p25 / p50 (L* / 100).

    Dark photos (low median) and backlit ones (dark lower quartile, bright rest) get lifted; a photo
    whose median and lower quartile are already at the targets gets 0 (untouched).
    `strength` (photo.auto_tone, 0–1) is the share of the way to the targets.
    """
    s = min(max(float(strength or 0), 0.0), 1.0)
    if s <= 0:
        return 0.0
    need = max(_lift_for(p50, TONE_TARGET_MID), _lift_for(p25, TONE_TARGET_SHADOW))
    return round(min(s * need, TONE_MAX_LIFT), 4)


def local_contrast_share(lift: float, strength: float) -> float:
    """How much local contrast (CLAHE on lightness) is blended in: none when nothing is lifted."""
    s = min(max(float(strength or 0), 0.0), 1.0)
    return TONE_LOCAL_CONTRAST * s * min(max(lift, 0.0) / TONE_LOCAL_FULL_LIFT, 1.0)


# Backlit / high-dynamic-range scenes (people in shadow in front of a bright window or sky): the median
# can be high, so the global curve leaves them alone; their shadows are lifted LOCALLY instead.
SHADOW_LEVEL = 0.28            # L* below this is "deep shadow"…
BRIGHT_LEVEL = 0.85            # …and above this "bright region"
SHADOW_SHARE = (0.08, 0.25)    # share of deep shadow where the backlit score starts / is full
BRIGHT_SHARE = (0.03, 0.12)    # share of bright pixels where it starts / is full
SHADOW_RECOVERY_MAX = 0.36     # max local shadow lift (L* / 100) at strength 1 (≈ +20 L* at 0.6)


def _smoothstep(x: float, lo: float, hi: float) -> float:
    t = min(max((float(x) - lo) / (hi - lo), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def backlit_score(shadow_share: float, bright_share: float) -> float:
    """0–1: how much a photo looks backlit (a real share of deep shadow AND a real bright region)."""
    return _smoothstep(shadow_share, *SHADOW_SHARE) * _smoothstep(bright_share, *BRIGHT_SHARE)


def shadow_recovery(shadow_share: float, bright_share: float, strength: float) -> float:
    """Maximum local shadow lift (L* / 100) for a photo with these shares of deep shadow (L* < 28) and
    bright pixels (L* > 85); 0 for photos that are not backlit (well exposed, or simply dark)."""
    s = min(max(float(strength or 0), 0.0), 1.0)
    return round(SHADOW_RECOVERY_MAX * s * backlit_score(shadow_share, bright_share), 4)


def shadow_weight(x):
    """How much of the local shadow lift a pixel of lightness x (L* / 100) gets: 0 at black (blacks stay
    black), 1 at x = 1/4 (faces in shadow), fading out in the highlights (a bright window: ≈ 0)."""
    return 9.4815 * x * (1 - x) ** 3


def auto_tone_setting(cfg: dict, shot: dict | None = None) -> float:
    """photo.auto_tone (0–1, 0 = off), overridable per shot with "auto_tone"."""
    v = (shot or {}).get("auto_tone", (cfg.get("photo") or {}).get("auto_tone", 0))
    try:
        return min(max(float(v or 0), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0
