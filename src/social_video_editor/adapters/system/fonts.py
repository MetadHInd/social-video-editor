"""Font files: user/system font folders (macOS, Linux and Windows) and the emoji font.

macOS keeps fonts flat in a few folders; Linux keeps them in sub-folders of /usr/share/fonts
(…/truetype/dejavu/, …/opentype/inter/…); Windows keeps them in %WINDIR%/Fonts and, for fonts
installed for one user, %LOCALAPPDATA%/Microsoft/Windows/Fonts, under short file names
(ariblk.ttf = Arial Black). The folders are indexed recursively once (case-insensitive file names).
When a requested font is missing, a bold sans-serif that is installed is used instead.
"""
from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

MACOS_FONT_DIRS = [Path.home() / "Library/Fonts", Path("/Library/Fonts"),
                   Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts")]
LINUX_FONT_DIRS = [Path.home() / ".local/share/fonts", Path.home() / ".fonts",
                   Path("/usr/local/share/fonts"), Path("/usr/share/fonts")]
FONT_EXTENSIONS = (".otf", ".ttf", ".ttc")


def windows_font_dirs(environ=None) -> list[Path]:
    """System fonts (%WINDIR%/Fonts) and the fonts installed for the current user only."""
    env = os.environ if environ is None else environ
    local = env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return [Path(env.get("WINDIR") or "C:\\Windows") / "Fonts", Path(local) / "Microsoft" / "Windows" / "Fonts"]


def platform_font_dirs(platform: str | None = None, environ=None) -> list[Path]:
    """Font folders searched on this OS. macOS and Linux folders are searched together (a missing
    folder costs nothing); Windows gets its own two folders (+ ~/.fonts, harmless)."""
    if (platform or sys.platform) == "win32":
        return windows_font_dirs(environ) + [Path.home() / ".fonts"]
    return MACOS_FONT_DIRS + LINUX_FONT_DIRS


FONT_DIRS = platform_font_dirs()

# Windows stores well-known fonts under short file names: lower-case font name → file name (no extension).
ALIASES = {"arial black": "ariblk", "arial bold": "arialbd", "segoe ui bold": "segoeuib",
           "segoe ui black": "seguibl", "segoe ui emoji": "seguiemj"}

# By preference: the macOS name first ("Arial Black" also finds Windows' ariblk), then Inter, Segoe UI
# Black (Windows), the usual Linux packages (DejaVu, Noto, Liberation) and the bold Windows fonts.
FALLBACK_FONTS = ["Arial Black", "ariblk", "Inter-Black", "seguibl", "DejaVuSans-Bold", "NotoSans-Black",
                  "NotoSans-Bold", "LiberationSans-Bold", "arialbd", "segoeuib", "DejaVuSans", "NotoSans-Regular"]
LABEL_FONTS = ["Arial Bold", "arialbd", "Inter-Bold", "segoeuib", "DejaVuSans-Bold", "NotoSans-Bold",
               "LiberationSans-Bold", "DejaVuSans", "NotoSans-Regular"]
EMOJI_FONTS = ["Apple Color Emoji", "NotoColorEmoji", "Noto Color Emoji", "TwemojiMozilla", "Twemoji",
               "JoyPixels", "EmojiOneColor", "seguiemj"]


@lru_cache(maxsize=1)
def _index() -> dict[str, Path]:
    """lower-case file name without extension → file, for every font under the font folders (recursive)."""
    out: dict[str, Path] = {}
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() in FONT_EXTENSIONS and p.is_file():
                out.setdefault(p.stem.lower(), p)
    return out


def _lookup_name(base: str) -> Path | None:
    direct = Path(base).expanduser()
    if direct.is_file():
        return direct
    for d in FONT_DIRS:
        for ext in FONT_EXTENSIONS:
            c = d / f"{base}{ext}"
            if c.is_file():
                return c
    return _index().get(base.lower())


def _lookup(base: str) -> Path | None:
    """A font file by path or by file name (no extension), then by its Windows file name; no fallback."""
    found = _lookup_name(base)
    alias = ALIASES.get(base.strip().lower())
    if found is None and alias:
        found = _lookup_name(alias)
    return found


def _first(names: list[str]) -> Path | None:
    return next((p for p in map(_lookup, names) if p is not None), None)


def split_face(name: str) -> tuple[str, int]:
    """'Name:2' → ('Name', 2). Only a trailing ':digits' is a face index, so the drive colon of a
    Windows path ('C:\\Fonts\\a.ttf') is kept."""
    m = re.fullmatch(r"(.+?):(\d+)", name)
    return (m[1], int(m[2])) if m else (name, 0)


def find_font_file(name: str) -> tuple[Path | None, int]:
    """Finds a font by file name (no extension) or path. 'Name:2' = face 2 of a .ttc. → (path, index).

    Missing fonts fall back to an installed bold sans-serif (face 0); (None, 0) when there is none."""
    base, idx = split_face(name)
    path = _lookup(base)
    if path is not None:
        return path, idx
    return _first(FALLBACK_FONTS), 0


@lru_cache(maxsize=1)
def label_font() -> Path | None:
    """Font for contact-sheet labels (Arial Bold on macOS/Windows, DejaVu/Noto/Liberation on Linux)."""
    return _first(LABEL_FONTS) or _first(FALLBACK_FONTS)


@lru_cache(maxsize=1)
def emoji_font() -> Path | None:
    """Color emoji font (Apple Color Emoji on macOS, Noto Color Emoji on Linux, Segoe UI Emoji on
    Windows); None if not installed."""
    return _first(EMOJI_FONTS)
