"""Font files: user/system font folders (macOS and Linux) and the emoji font.

macOS keeps fonts flat in a few folders; Linux keeps them in sub-folders of /usr/share/fonts
(…/truetype/dejavu/, …/opentype/inter/…), so those folders are indexed recursively once.
When a requested font is missing, a bold sans-serif that is installed is used instead.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

MACOS_FONT_DIRS = [Path.home() / "Library/Fonts", Path("/Library/Fonts"),
                   Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts")]
LINUX_FONT_DIRS = [Path.home() / ".local/share/fonts", Path.home() / ".fonts",
                   Path("/usr/local/share/fonts"), Path("/usr/share/fonts")]
FONT_DIRS = MACOS_FONT_DIRS + LINUX_FONT_DIRS
FONT_EXTENSIONS = (".otf", ".ttf", ".ttc")

# By preference: macOS name first, then the usual Linux packages (DejaVu, Noto, Liberation).
FALLBACK_FONTS = ["Arial Black", "Inter-Black", "DejaVuSans-Bold", "NotoSans-Black", "NotoSans-Bold",
                  "LiberationSans-Bold", "DejaVuSans", "NotoSans-Regular"]
LABEL_FONTS = ["Arial Bold", "Inter-Bold", "DejaVuSans-Bold", "NotoSans-Bold", "LiberationSans-Bold",
               "DejaVuSans", "NotoSans-Regular"]
EMOJI_FONTS = ["Apple Color Emoji", "NotoColorEmoji", "Noto Color Emoji", "TwemojiMozilla", "Twemoji",
               "JoyPixels", "EmojiOneColor"]


@lru_cache(maxsize=1)
def _index() -> dict[str, Path]:
    """lower-case file name without extension → file, for every font under the Linux font folders."""
    out: dict[str, Path] = {}
    for d in LINUX_FONT_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() in FONT_EXTENSIONS and p.is_file():
                out.setdefault(p.stem.lower(), p)
    return out


def _lookup(base: str) -> Path | None:
    """A font file by path or by file name (no extension), without fallback."""
    direct = Path(base).expanduser()
    if direct.is_file():
        return direct
    for d in FONT_DIRS:
        for ext in FONT_EXTENSIONS:
            c = d / f"{base}{ext}"
            if c.is_file():
                return c
    return _index().get(base.lower())


def _first(names: list[str]) -> Path | None:
    return next((p for p in map(_lookup, names) if p is not None), None)


def find_font_file(name: str) -> tuple[Path | None, int]:
    """Finds a font by file name (no extension). 'Name:2' = face 2 of a .ttc. → (path, index).

    Missing fonts fall back to an installed bold sans-serif (face 0); (None, 0) when there is none."""
    base, _, idx = name.partition(":")
    path = _lookup(base)
    if path is not None:
        return path, int(idx or 0)
    return _first(FALLBACK_FONTS), 0


@lru_cache(maxsize=1)
def label_font() -> Path | None:
    """Font for contact-sheet labels (Arial Bold on macOS, DejaVu/Noto/Liberation on Linux)."""
    return _first(LABEL_FONTS) or _first(FALLBACK_FONTS)


@lru_cache(maxsize=1)
def emoji_font() -> Path | None:
    """Color emoji font (Apple Color Emoji on macOS, Noto Color Emoji on Linux); None if not installed."""
    return _first(EMOJI_FONTS)
