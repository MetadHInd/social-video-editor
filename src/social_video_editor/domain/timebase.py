"""Time helpers: frame rounding and mm:ss formatting."""
from __future__ import annotations

DEFAULT_FPS = 30


def frames(sec: float, fps: int = DEFAULT_FPS) -> float:
    """Round to whole frames (avoids drift at the joins)."""
    return round(sec * fps) / fps


def mmss(sec: float) -> str:
    m, s = divmod(max(sec, 0), 60)
    return f"{int(m):02d}:{s:05.2f}"
