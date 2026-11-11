"""`sve doctor`: what the engine needs on this machine and what is missing.

Every check gives (status, name, detail): "ok", "missing" (required: renders will fail) or
"optional" (a part that is not installed; the hint says how to get it). Nothing is installed,
downloaded or loaded here; heavy libraries are only looked up, not imported.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

from ..files.workspace import subdir, workspace
from . import fonts
from .tools import FFMPEG, FFPROBE, ffmpeg_version, user_cache_dir

# FFmpeg filters and encoders the engine's commands use
REQUIRED_FILTERS = ("xfade", "overlay", "scale", "crop", "pad", "fps", "format", "eq", "lut3d", "colorspace",
                    "vibrance", "colorbalance", "colortemperature", "colorchannelmixer", "cas", "boxblur", "vignette",
                    "tpad", "fade", "ebur128", "alimiter", "acompressor", "sidechaincompress", "afftdn", "deesser",
                    "equalizer", "highpass", "atempo", "amix", "acrossfade", "blackdetect", "freezedetect")
REQUIRED_ENCODERS = ("libx264", "aac")
OPTIONAL_PARTS = (     # (name, module to look for, what it is for, how to get it)
    ("graphics", "skia", "animated graphics (sve graphic, graphic shots)", 'pip install "social-video-editor[graphics]"'),
    ("vision", "sentence_transformers", "product detection and search by text", 'pip install "social-video-editor[vision]"'),
    ("enhance", "spandrel", "enlarging small photos with a model (sve enhance)", 'pip install "social-video-editor[enhance]"'),
    ("music download", "yt_dlp", "sve music", 'pip install "social-video-editor[music]"'),
)
Check = tuple[str, str, str]


def _listing(flag: str) -> str:
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", flag], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout or ""


def listed_names(text: str) -> set[str]:
    """Names in an `ffmpeg -filters` / `-encoders` listing (the second column of each entry)."""
    names = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith(("Filters:", "Encoders:", " ---", " ===")):
            names.add(parts[1])
    return names


def ffmpeg_checks() -> list[Check]:
    if not (shutil.which(FFMPEG) or Path(FFMPEG).is_file()):
        return [("missing", "FFmpeg", "not found: install FFmpeg 7 or newer and put ffmpeg / ffprobe on PATH")]
    version = ffmpeg_version()
    out: list[Check] = [("ok", "FFmpeg", f"{'.'.join(map(str, version)) if version else 'version unknown'} ({FFMPEG})")]
    if not (shutil.which(FFPROBE) or Path(FFPROBE).is_file()):
        out.append(("missing", "ffprobe", "not found next to ffmpeg"))
    if version and version < (7, 0):
        out.append(("missing", "FFmpeg version", "the engine needs FFmpeg 7 or newer"))
    filters, encoders = listed_names(_listing("-filters")), listed_names(_listing("-encoders"))
    lacking = [f for f in REQUIRED_FILTERS if f not in filters]
    out.append(("missing", "FFmpeg filters", f"this build lacks: {', '.join(lacking)}") if lacking else
               ("ok", "FFmpeg filters", f"all {len(REQUIRED_FILTERS)} the engine uses"))
    lacking = [e for e in REQUIRED_ENCODERS if e not in encoders]
    out.append(("missing", "FFmpeg encoders", f"this build lacks: {', '.join(lacking)}") if lacking else
               ("ok", "FFmpeg encoders", ", ".join(REQUIRED_ENCODERS)))
    out.append(("ok", "Rubber Band", "time-stretch for AutoMix") if "rubberband" in filters or shutil.which("rubberband")
               else ("optional", "Rubber Band", "AutoMix beat matching uses it when present (brew / apt install rubberband)"))
    return out


def font_checks() -> list[Check]:
    text, _ = fonts.find_font_file("")
    emoji = fonts.emoji_font()
    return [("ok", "Text font", str(text)) if text else
            ("missing", "Text font", "no bold sans-serif font found (install Inter or DejaVu Sans)"),
            ("ok", "Emoji font", str(emoji)) if emoji else
            ("optional", "Emoji font", "emoji in texts are drawn only with a color emoji font (e.g. Noto Color Emoji)")]


def part_checks(graphics=None, visual=None, upscaler=None, settings: dict | None = None) -> list[Check]:
    settings = settings or {}
    out: list[Check] = []
    for name, module, purpose, hint in OPTIONAL_PARTS:
        if importlib.util.find_spec(module) is None:
            out.append(("optional", name, f"not installed — {purpose}: {hint}"))
            continue
        detail = purpose
        if name == "vision" and visual is not None and not visual.downloaded(settings.get("vision") or {}):
            out.append(("optional", name, "installed; the models (≈1.2 GB) are downloaded the first time: sve products <project>"))
            continue
        if name == "enhance" and upscaler is not None:
            why = upscaler.ready(settings.get("enhance") or {})
            if why:
                out.append(("optional", name, f"installed, not ready — {why}"))
                continue
        out.append(("ok", name, detail))
    out.append(("ok", "speech to text", "faster-whisper") if importlib.util.find_spec("faster_whisper") else
               ("missing", "speech to text", "faster-whisper is not installed (pip install faster-whisper)"))
    return out


def workspace_checks(packs: list[str] | None = None) -> list[Check]:
    ws = workspace()
    projects = subdir("projects")
    n = sum(1 for p in projects.iterdir() if p.is_dir()) if projects.is_dir() else 0
    out: list[Check] = [("ok", "Workspace", f"{ws} ({n} projects)")]
    if packs is not None:
        out.append(("ok", "Packs", ", ".join(packs)) if packs else
                   ("optional", "Packs", "none (packs/<name>/pack.yaml gives graphics a channel's look)"))
    cache = user_cache_dir()
    out.append(("ok", "User cache", str(cache)))
    for var, purpose in (("SVE_WORKSPACE", "workspace folder"), ("SVE_JOBS", "shots rendered at once"),
                         ("SVE_HWACCEL", "hardware acceleration")):
        if os.environ.get(var):
            out.append(("ok", f"${var}", f"{os.environ[var]} ({purpose})"))
    return out


def diagnose(svc=None, settings: dict | None = None) -> list[Check]:
    """Every check, in the order they are shown."""
    packs = svc.packs.available() if svc is not None and getattr(svc, "packs", None) is not None else None
    return [*ffmpeg_checks(), *font_checks(),
            *part_checks(getattr(svc, "graphics", None), getattr(svc, "visual", None), getattr(svc, "upscaler", None), settings),
            *workspace_checks(packs)]


def doctor_lines(checks: list[Check]) -> list[str]:
    mark = {"ok": "OK ", "missing": "!! ", "optional": "-- "}
    lines = [f"{mark[status]} {name}: {detail}" for status, name, detail in checks]
    missing = sum(1 for c in checks if c[0] == "missing")
    optional = sum(1 for c in checks if c[0] == "optional")
    lines.append(f"{'Ready' if not missing else f'{missing} problem(s) to fix'}"
                 f"{f' · {optional} optional part(s) not set up' if optional else ''}")
    return lines
