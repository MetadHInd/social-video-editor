"""What a graphic draws on: a small canvas interface (implemented by the Skia adapter).

Templates only use these calls, so they run without any drawing library (RecordingCanvas keeps the
calls in a list: used by the tests and to check a graphic without rendering it).
Colors are "#RRGGBB"; fonts are roles of the theme ("display", "number", "label").
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Protocol

from .paths import Path


class Canvas(Protocol):
    W: int
    H: int

    def save(self) -> None: ...
    def restore(self) -> None: ...
    def translate(self, dx: float, dy: float) -> None: ...
    def rotate(self, degrees: float, cx: float = 0.0, cy: float = 0.0) -> None: ...
    def scale(self, sx: float, sy: float | None = None, cx: float = 0.0, cy: float = 0.0) -> None: ...
    def push_alpha(self, alpha: float) -> None:
        """Everything drawn until the matching restore() is composited as one layer at `alpha`."""
    def clip(self, path: Path) -> None: ...
    def fill(self, path: Path, color: str, alpha: float = 1.0) -> None: ...
    def stroke(self, path: Path, color: str, width: float, alpha: float = 1.0, cap: str = "round",
               join: str = "round") -> None: ...
    def halftone(self, path: Path, color: str, cell: float, angle: float, radius: float, alpha: float = 1.0) -> None:
        """Fills the path with a grid of dots (a printed shadow instead of a blurred one)."""
    def text(self, text: str, x: float, y: float, font: str, size: float, color: str, alpha: float = 1.0) -> None:
        """One line; (x, y) is the left end of the baseline."""
    def text_path(self, text: str, x: float, y: float, font: str, size: float) -> Path:
        """The outline of one line of text, to fill / stroke / move like any path."""
    def measure(self, text: str, font: str, size: float) -> float: ...
    def image(self, file: str, x: float, y: float, w: float, h: float, alpha: float = 1.0) -> None:
        """An image fitted inside the box (aspect kept, centered)."""
    def sticker(self, file: str, x: float, y: float, w: float, h: float, border: float, border_color: str,
                outline: float, outline_color: str) -> None:
        """An image as a die-cut sticker: a border that follows its silhouette (or its frame when
        it has no transparency) and an outline around the border."""


@contextmanager
def saved(cv: Canvas):
    cv.save()
    try:
        yield cv
    finally:
        cv.restore()


def fake_measure(text: str, font: str, size: float) -> float:
    """Text width without a font engine (0.55 em per character); enough to lay out and test."""
    return len(text) * size * 0.55


class RecordingCanvas:
    """A canvas that draws nothing and remembers every call as (name, args…)."""

    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        self.calls: list[tuple] = []

    def _rec(self, name: str, *args) -> None:
        self.calls.append((name, *args))

    def save(self): self._rec("save")
    def restore(self): self._rec("restore")
    def translate(self, dx, dy): self._rec("translate", dx, dy)
    def rotate(self, degrees, cx=0.0, cy=0.0): self._rec("rotate", degrees, cx, cy)
    def scale(self, sx, sy=None, cx=0.0, cy=0.0): self._rec("scale", sx, sx if sy is None else sy, cx, cy)
    def push_alpha(self, alpha): self._rec("push_alpha", alpha)
    def clip(self, path): self._rec("clip", path)
    def fill(self, path, color, alpha=1.0): self._rec("fill", path, color, alpha)

    def stroke(self, path, color, width, alpha=1.0, cap="round", join="round"):
        self._rec("stroke", path, color, width, alpha)

    def halftone(self, path, color, cell, angle, radius, alpha=1.0):
        self._rec("halftone", path, color, cell, angle, radius, alpha)

    def text(self, text, x, y, font, size, color, alpha=1.0):
        self._rec("text", text, x, y, font, size, color, alpha)

    def text_path(self, text, x, y, font, size) -> Path:
        self._rec("text_path", text, x, y, font, size)
        return Path(native=("text", text, x, y, font, size))

    def measure(self, text, font, size) -> float:
        return fake_measure(text, font, size)

    def image(self, file, x, y, w, h, alpha=1.0): self._rec("image", file, x, y, w, h, alpha)

    def sticker(self, file, x, y, w, h, border, border_color, outline, outline_color):
        self._rec("sticker", file, x, y, w, h, border)

    # helpers for assertions
    def texts(self) -> list[str]:
        """Every string drawn (as text or as an outline), in order, without repeats."""
        return list(dict.fromkeys(c[1] for c in self.calls if c[0] in ("text", "text_path")))

    def count(self, name: str) -> int:
        return sum(1 for c in self.calls if c[0] == name)
