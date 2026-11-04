"""Where FFmpeg / ffprobe live and where generated files are cached (macOS, Linux and Windows)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# Homebrew's folders are not always on PATH for apps started outside a login shell (macOS only).
HOMEBREW_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")

MISSING_FFMPEG = (
    "FFmpeg was not found. Install FFmpeg 7 or newer and put ffmpeg/ffprobe on PATH:\n"
    "  Windows: winget install --id Gyan.FFmpeg -e   (then open a new terminal)\n"
    "  macOS:   brew install ffmpeg\n"
    "  Linux:   sudo apt install ffmpeg   (or your distribution's package)"
)


def _find(name: str, platform: str | None = None) -> str:
    """Full path of an FFmpeg tool. shutil.which honours PATHEXT, so it finds ffmpeg.exe on Windows.

    Falls back to Homebrew's folders on macOS only; elsewhere the bare name is returned so the
    error (FileNotFoundError) names the missing tool instead of a path that never existed."""
    found = shutil.which(name)
    if found:
        return found
    if (platform or sys.platform) == "darwin":
        for d in HOMEBREW_BIN_DIRS:
            p = Path(d) / name
            if p.is_file():
                return str(p)
    return name


FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")


def parse_ffmpeg_version(text: str) -> tuple[int, int] | None:
    """(major, minor) from `ffmpeg -version` output ("ffmpeg version 7.1.1-1ubuntu4", "… n8.1-…");
    None for builds without a release number (git snapshots "N-123-g…", dated builds)."""
    m = re.search(r"ffmpeg version n?(\d+)\.(\d+)", text)
    return (int(m[1]), int(m[2])) if m else None


@lru_cache(maxsize=1)
def ffmpeg_version() -> tuple[int, int] | None:
    """Release (major, minor) of the FFmpeg the engine runs; None when unknown."""
    try:
        # explicit UTF-8: the default is the ANSI code page on Windows, which cannot decode every banner
        r = subprocess.run([FFMPEG, "-hide_banner", "-version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ffmpeg_version(r.stdout or "")


def user_cache_dir(environ=None, platform: str | None = None) -> Path:
    """User cache for generated files (LUTs, models…), read at call time.

    $XDG_CACHE_HOME wins on every OS (tests and the Docker image set it); otherwise %LOCALAPPDATA% on
    Windows (~/AppData/Local when unset) and ~/.cache on macOS/Linux. → <base>/social_video_editor"""
    env = os.environ if environ is None else environ
    plat = platform or sys.platform
    if env.get("XDG_CACHE_HOME"):
        base = Path(env["XDG_CACHE_HOME"])
    elif plat == "win32":
        base = Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path.home() / ".cache"
    return base.expanduser() / "social_video_editor"


def configure_numba_cache(environ=None) -> None:
    """Points numba's cache (used by librosa) to the user cache unless NUMBA_CACHE_DIR is already set.

    numba otherwise writes next to the installed package, which is read-only in many installs (e.g.
    Program Files on Windows). Only sets the variable: no folder is created and numba is not imported."""
    env = os.environ if environ is None else environ
    env.setdefault("NUMBA_CACHE_DIR", str(user_cache_dir(env) / "numba"))
