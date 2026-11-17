"""Use case: subtitles drawn in the graphics' narration box (`subtitles.style: caption`,
domain/caption_subtitles.py) — one transparent clip as long as the video, laid over it like a
graphic overlay, cached by its words, times, theme and frame size."""
from __future__ import annotations

import hashlib
import json

from ..domain import caption_subtitles as cs
from ..domain.models import Project
from ..log import log
from .graphic import graphics_theme
from .services import Services

VERSION = 1


def wanted(svc: Services, cfg: dict) -> bool:
    """Whether the subtitles are drawn in the narration box (the setting, and graphics can be drawn)."""
    if cs.style(cfg) != cs.CAPTION:
        return False
    why = svc.graphics.ready() if svc.graphics is not None else "no graphic renderer"
    if why:
        log(f"Subtitles: the caption style needs the graphics ({why}); drawing the default subtitles")
        return False
    return True


def render_caption_subtitles(svc: Services, project: Project, cfg: dict, groups: list[dict], W: int, H: int,
                             total: float, draft: bool, boxes: list | None = None) -> dict | None:
    """The subtitle track as a transparent clip → a compose cutaway {"file", "start", "end", "alpha"}
    (None without groups). `boxes` receives where every group is drawn (summary text_boxes)."""
    if not groups:
        return None
    fps = int(cfg["output"]["fps"])
    n = int(round(total * fps))
    theme = graphics_theme(svc, cfg)
    spec = cs.track_spec(groups, cfg, W, H, n / fps)
    track = cs.SubtitleTrack(spec, svc.graphics.stage(W, H, theme, cfg.get("language", "es")))
    if boxes is not None:
        boxes += track.text_boxes()
    key = json.dumps({"spec": spec, "theme": theme, "W": W, "H": H, "fps": fps, "draft": draft, "v": VERSION},
                     sort_keys=True, default=str)
    path = svc.cache.file(project.cache, f"segments/subs_{hashlib.sha1(key.encode()).hexdigest()[:14]}.mov")
    if not svc.cache.has(path):
        log(f"Subtitles: {len(groups)} groups in the narration box ({total:.1f} s)…")
        frames = (f.tobytes() for f in svc.graphics.overlay_frames(track, fps, n))
        svc.encoder.encode_overlay(frames, n, W, H, fps, path)
    return {"file": path, "start": 0.0, "end": round(n / fps, 4), "alpha": True}
