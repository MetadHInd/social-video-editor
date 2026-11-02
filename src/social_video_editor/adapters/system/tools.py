"""Where FFmpeg / ffprobe live and where generated files are cached."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def user_cache_dir() -> Path:
    """User cache for generated files (LUTs…): $XDG_CACHE_HOME/social_video_editor or ~/.cache/…"""
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base).expanduser() / "social_video_editor"
