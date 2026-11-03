"""Where FFmpeg / ffprobe live and where generated files are cached."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def parse_ffmpeg_version(text: str) -> tuple[int, int] | None:
    """(major, minor) from `ffmpeg -version` output ("ffmpeg version 7.1.1-1ubuntu4", "… n8.1-…");
    None for builds without a release number (git snapshots "N-123-g…", dated builds)."""
    m = re.search(r"ffmpeg version n?(\d+)\.(\d+)", text)
    return (int(m[1]), int(m[2])) if m else None


@lru_cache(maxsize=1)
def ffmpeg_version() -> tuple[int, int] | None:
    """Release (major, minor) of the FFmpeg the engine runs; None when unknown."""
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", "-version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ffmpeg_version(r.stdout)


def user_cache_dir() -> Path:
    """User cache for generated files (LUTs…): $XDG_CACHE_HOME/social_video_editor or ~/.cache/…"""
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base).expanduser() / "social_video_editor"
