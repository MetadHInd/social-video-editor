"""The picture of a source that has none (a voice memo): the graphics' own moving backdrop — the
grid, circuit traces with pulses, blinking chips of the theme or pack (kit.scenery) — so a stretch
that no graphic or clip covers is never black. A pack may register an element "backdrop"
(`fn(cv, st, x, y, t, **options)`, x, y = the middle of the frame) to draw a place of its own over it."""
from __future__ import annotations

from .graphics.canvas import Canvas
from .graphics.scene import ELEMENTS, Scene


def has_picture(info: dict) -> bool:
    """Whether a media source has a picture: False only for sound without a video stream (a voice
    memo: ffprobe found audio and no width)."""
    return bool(info.get("image")) or bool(info.get("width")) or not info.get("has_audio")


class Backdrop(Scene):
    """Only the stage: Scene.paint draws the scenery behind every full-frame graphic, nothing on it —
    unless a pack registered a "backdrop" element, drawn over it."""

    name = "backdrop"
    scales = (1.0,)

    def layout(self) -> None:
        self.block = (0.0, 0.0)
        self.settle = 0.0

    def draw(self, cv: Canvas, t: float) -> None:
        drawn = ELEMENTS.get("backdrop")
        if drawn is not None:
            drawn(cv, self.st, self.st.W / 2, self.st.H / 2, t, dur=self.dur)

    def sounds(self) -> list[tuple[str, float]]:
        return []
