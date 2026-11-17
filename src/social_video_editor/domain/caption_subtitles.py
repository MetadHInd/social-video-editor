"""Subtitles drawn as the graphics' narration box (`subtitles.style: caption`): every subtitle group
(domain/text_layout.subtitle_groups: the same words, timing and splitting as the default subtitles)
is a paper box from the pack's theme (kit.caption), the word being said marked with an ink band that
grows under it. The whole track is one transparent graphic laid over the video (like `callouts`).

Where the box goes: its middle at `subtitles.y` of the height, centered, as wide as the default
subtitles may be (text_layout.text_width · 0.86), kept inside the safe zone that `sve check` measures
(between its top and bottom, off the right-hand rail of the apps' buttons in a vertical frame).
"""
from __future__ import annotations

from . import check
from .graphics import kit, motion
from .graphics.canvas import Canvas, RecordingCanvas
from .graphics.scene import Scene
from .text_layout import text_scale, text_width

STYLES = ("pillow", "caption")      # subtitles.style: the default Pillow layer, or the narration box
CAPTION = "caption"
SIZE = 1.0                          # the box's letters: this share of subtitles.size
MARK_SECONDS = 0.12                 # the band under a word grows in this long
MARK_STEPS = 4                      # …in this many drawings (frames in between reuse them)
WIDTH = 0.86                        # as wide as the default subtitles' lines may be


def style(cfg: dict) -> str:
    value = str((cfg.get("subtitles") or {}).get("style") or "pillow").strip().lower()
    return value if value in STYLES else "pillow"


def safe_band(cfg: dict, W: int, H: int) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) in pixels: where `sve check` wants every text box."""
    z = check.safe_zone(W, H, check.thresholds(cfg.get("check")))
    left = (z["left"] or 0.0) * W
    right = W - (z["right"] or z["left"] or 0.0) * W
    return left, z["top"] * H, right, z["bottom"] * H


class SubtitleTrack(Scene):
    """The subtitle groups of a whole video, drawn in the narration box. spec: {"groups": [{"words":
    [{"word", "start", "end"}], "start", "end"}], "dur", "y" (0-1), "size" (px), "uppercase", "band":
    [left, top, right, bottom] (px)}."""

    name = "subtitles"
    overlay = True
    scales = (1.0,)

    def layout(self) -> None:
        sp, st = self.spec, self.st
        self.groups = [g for g in sp.get("groups") or [] if g.get("words")]
        upper = bool(sp.get("uppercase", True))
        self.texts = [[(str(w["word"]).upper() if upper else str(w["word"])) for w in g["words"]] for g in self.groups]
        self.size = float(sp.get("size") or st.px(58))
        left, top, right, bottom = (float(v) for v in sp.get("band") or (0, 0, st.W, st.H))
        mid = (left + right) / 2
        half = min(mid - left, right - mid)
        self.cx, self.width = mid, min(float(sp.get("width") or st.W), 2 * half)
        self.boxes: list[tuple[float, float, float, float]] = []
        cy = float(sp.get("y", 0.64)) * st.H
        for i, words in enumerate(self.texts):
            _, _, w, h = kit.caption(RecordingCanvas(st.W, st.H), st, " ".join(words), 0.0, i, x=self.cx, y=0.0,
                                     width=self.width, anchor="center", size=self.size)
            y = min(max(cy - h / 2, top), bottom - h)
            self.boxes.append((self.cx - w / 2, y, w, h))
        self.block = (0.0, 0.0)
        self.settle = 0.0

    def state(self, t: float) -> tuple[int, int, float] | None:
        """(group, word being said, how far its band has grown) at `t`, or None between groups."""
        for i, g in enumerate(self.groups):
            if g["start"] <= t < g["end"]:
                words = g["words"]
                k = max((j for j, w in enumerate(words) if w["start"] <= t), default=0)
                since = t - (g["start"] if k == 0 else words[k]["start"])
                mark = min(int(motion.clamp01(since / MARK_SECONDS) * MARK_STEPS + 1e-9), MARK_STEPS) / MARK_STEPS
                return i, k, mark
        return None

    def frame_state(self, t: float) -> tuple[tuple, float]:
        # never "settled": the renderer keeps only the last drawing of a moving state, so a long video
        # holds one picture in memory, and the frames of one state reuse it
        return ("moving", self.state(t)), t

    def draw(self, cv: Canvas, t: float) -> None:
        s = self.state(t)
        if s is None:
            return
        i, k, mark = s
        x, y, w, h = self.boxes[i]
        kit.caption(cv, self.st, " ".join(self.texts[i]), t, i, highlight=k, x=self.cx, y=y, width=self.width,
                    anchor="center", size=self.size, mark=mark)

    def sounds(self) -> list[tuple[str, float]]:
        return []

    def text_boxes(self) -> list[dict]:
        """Where every group was drawn, for summary.json (`text_boxes`, kind "subtitle")."""
        return [{"kind": "subtitle", "text": " ".join(words), "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                 "start": round(float(g["start"]), 3), "end": round(float(g["end"]), 3)}
                for g, words, (x, y, w, h) in zip(self.groups, self.texts, self.boxes)]


def track_spec(groups: list[dict], cfg: dict, W: int, H: int, total: float) -> dict:
    """The SubtitleTrack spec for a video: its groups, place and size from the subtitle settings."""
    s = cfg.get("subtitles") or {}
    scale = text_scale(W, H)
    return {"template": "subtitles", "dur": round(float(total), 4),
            "groups": [{"words": [{"word": w["word"], "start": round(float(w["start"]), 3),
                                   "end": round(float(w["end"]), 3)} for w in g["words"]],
                        "start": round(float(g["start"]), 3), "end": round(float(g["end"]), 3)} for g in groups],
            "y": float(s.get("y", 0.64)), "size": round(float(s.get("size", 82)) * scale * SIZE, 2),
            "uppercase": bool(s.get("uppercase", True)), "width": round(text_width(W, H) * WIDTH, 1),
            "band": [round(v, 1) for v in safe_band(cfg, W, H)]}
