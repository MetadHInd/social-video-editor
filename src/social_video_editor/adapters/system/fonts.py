"""Font files: user/system font folders and the emoji font."""
from __future__ import annotations

from pathlib import Path

FONT_DIRS = [Path.home() / "Library/Fonts", Path("/Library/Fonts"),
             Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts")]
FALLBACK_FONT = Path("/System/Library/Fonts/Supplemental/Arial Black.ttf")
LABEL_FONT = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
EMOJI_FONT = Path("/System/Library/Fonts/Apple Color Emoji.ttc")


def find_font_file(name: str) -> tuple[Path | None, int]:
    """Finds a font by file name (no extension). 'Name:2' = face 2 of a .ttc. → (path, index)."""
    base, _, idx = name.partition(":")
    cand = [Path(base).expanduser()] + [d / f"{base}{ext}" for d in FONT_DIRS for ext in (".otf", ".ttf", ".ttc")]
    cand.append(FALLBACK_FONT)
    for c in cand:
        if c.exists() and c.is_file():
            return c, int(idx or 0)
    return None, 0
