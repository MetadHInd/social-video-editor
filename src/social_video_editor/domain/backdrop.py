"""The picture of a source that has none (a voice memo): the graphics' own moving backdrop — the
grid, circuit traces with pulses, blinking chips of the theme or pack (kit.scenery) — so a stretch
that no graphic or clip covers is never black."""
from __future__ import annotations

from .graphics.canvas import Canvas
from .graphics.scene import Scene


def has_picture(info: dict) -> bool:
    """Whether a media source has a picture (a photo or a video stream); a voice memo has none."""
    return bool(info.get("image")) or bool(info.get("width"))


class Backdrop(Scene):
    """Only the stage: Scene.paint draws the scenery behind every full-frame graphic, nothing on it."""

    name = "backdrop"
    scales = (1.0,)

    def layout(self) -> None:
        self.block = (0.0, 0.0)
        self.settle = 0.0

    def draw(self, cv: Canvas, t: float) -> None:
        return None

    def sounds(self) -> list[tuple[str, float]]:
        return []
