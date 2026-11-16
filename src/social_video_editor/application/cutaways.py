"""Use case: cutaways — the plan's pictures over the main track (domain/cutaways.py), rendered as
silent pieces the size of the video and laid over it when the video is composed. The sound of the
main track is never touched: the presenter keeps talking under a graphic or a photo.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..domain import color_math, footage
from ..domain import pip as pip_window
from ..domain.cutaways import DEFAULT_ENTRANCE, PHOTO_ENTRANCE, Cutaway, kind_of, resolve, soften, spec_of
from ..domain.explainer import script_settings
from ..domain.models import PlanError, Project
from ..domain.photos import ALL_MOTIONS, auto_tone_setting, photo_crf, photo_settings
from ..domain.timeline import Timeline
from ..log import log
from .graphic import build_scene, graphics_theme
from .services import Services

CUTAWAY_VERSION = 2
PHOTO_MOTION = "zoom_in"
CLIP_MIN_SPEED = 0.5        # a clip shorter than its time on screen is slowed down, at most to this
CLIP_KEYS = ("focus_x", "focus_y", "zoom", "color", "fit")     # shot keys a clip cutaway may carry


@dataclass
class RenderedCutaways:
    files: list[dict] = field(default_factory=list)       # for compose: clips {"file", "start", "end", "alpha"} and
    #                                                         presenter windows {"pip", "start", "end", "crop", "rect"}
    sounds: list[tuple[str, float, float]] = field(default_factory=list)   # (name, second, gain dB)
    details: list[dict] = field(default_factory=list)     # for summary.json
    warnings: list[str] = field(default_factory=list)
    credits: list[dict] = field(default_factory=list)     # footage/credits.json records of the clips shown


def entrance_of(svc: Services, cfg: dict, W: int, H: int, theme: dict | None = None) -> Callable[[dict], float]:
    """entrance(plan cutaway) → seconds it takes to come in: a graphic's `settle` (measured by laying
    it out; an estimate when graphics cannot be drawn here), a short moment for photos and clips."""
    ready = svc.graphics is not None and not svc.graphics.ready()
    known: dict[str, float] = {}
    themes = [theme]

    def entrance(cutaway: dict) -> float:
        if kind_of(cutaway) != "graphic":
            return PHOTO_ENTRANCE
        if not ready:
            return DEFAULT_ENTRANCE
        spec = {k: v for k, v in spec_of(cutaway).items() if k != "dur"}
        key = json.dumps(spec, sort_keys=True, default=str)
        if key not in known:
            if themes[0] is None:
                themes[0] = graphics_theme(svc, cfg)
            known[key] = float(build_scene(svc, cfg, spec, W, H, themes[0]).settle)
        return known[key]
    return entrance


def place_cutaways(svc: Services, cfg: dict, plan: dict, tl: Timeline, W: int, H: int,
                   theme: dict | None = None) -> tuple[list[Cutaway], list[str]]:
    """The plan's cutaways on the timeline → (cutaways, warnings)."""
    if not plan.get("cutaways"):
        return [], []
    return resolve(plan, tl, int(cfg["output"]["fps"]), entrance_of(svc, cfg, W, H, theme), pip_window.settings(cfg))


def render_cutaways(svc: Services, project: Project, cfg: dict, plan: dict, tl: Timeline, W: int, H: int,
                    draft: bool, infos: list[dict] | None = None, analyses: list[dict] | None = None) -> RenderedCutaways:
    """Renders every cutaway to a cached silent segment (cache/segments/cut_*).

    Full-frame cutaways go first, each followed by the presenter's window when it has one (the window
    and its paper frame), then the overlays: that is the order they are laid over the video."""
    out = RenderedCutaways()
    if not plan.get("cutaways"):
        return out
    fps = int(cfg["output"]["fps"])
    theme = graphics_theme(svc, cfg) if any(kind_of(c, i) == "graphic" for i, c in enumerate(plan["cutaways"])) else None
    placed, out.warnings = place_cutaways(svc, cfg, plan, tl, W, H, theme)
    sound_on = cfg["sfx"].get("enabled", True)
    base = float(cfg["sfx"].get("volume_db", -12))
    records = None                                         # footage/credits.json, read for the first clip
    for c in sorted(placed, key=lambda c: (c.overlay, c.start, c.index)):
        n = int(round((c.end - c.start) * fps))
        dur = n / fps
        window = _window(cfg, tl, c, W, H, infos, analyses) if c.pip else None
        extra: dict = {}
        framed = bool(window) and svc.graphics is not None and not svc.graphics.ready()    # its paper frame is a graphic
        log(f"  cutaway {c.index + 1}/{len(plan['cutaways'])}: {c.kind} {c.label} "
            f"{c.start:.2f}–{c.end:.2f} s{' (over the picture)' if c.overlay else ''}"
            f"{' + presenter window' if window else ''}")
        try:
            if c.kind == "graphic":
                shaped = theme if not window else {**theme, "safe": {"vertical": window["safe"], "landscape": window["safe"]}}
                path = _graphic(svc, project, cfg, c, n, W, H, fps, shaped, draft, out, sound_on, base)
            elif c.kind == "photo":
                path = _photo(svc, project, cfg, c, dur, W, H, fps, draft)
            else:
                if not c.spec.get("file"):
                    out.warnings.append(f"cutaway {c.index} ({c.label}): no clip for it yet (run `sve broll "
                                        f"{project.name}`); the presenter stays on screen")
                    continue
                path, short = _clip(svc, project, cfg, c, n, W, H, fps, draft)
                if short:
                    out.warnings.append(f"cutaway {c.index} ({c.label}): the clip is shorter than its {dur:.1f} s "
                                        "on screen; the presenter comes back before it ends")
                if records is None:
                    records = footage_records(svc, project)
                extra = _footage_detail(svc, project, records, str(c.spec["file"]), out)
        except PlanError as e:
            raise PlanError(f"Cutaway {c.index} ({c.kind} {c.label}): {e}") from None
        out.files.append({"file": path, "start": c.start, "end": c.end, "alpha": c.overlay})
        if window:
            out.files.append({"pip": True, "start": c.start, "end": c.end, "crop": window["crop"], "rect": window["rect"]})
        if framed:
            frame = Cutaway(c.index, "graphic", {"template": "pip_frame", "rect": list(window["frame"])}, c.start, c.end, True)
            if theme is None:
                theme = graphics_theme(svc, cfg)
            out.files.append({"file": _graphic(svc, project, cfg, frame, n, W, H, fps, theme, draft, out, False, base),
                              "start": c.start, "end": c.end, "alpha": True})
        out.details.append({"n": c.index, "type": c.kind, "what": c.label, "start": c.start, "end": c.end,
                            "overlay": c.overlay, **({"sentences": list(c.sentences)} if c.sentences else {}),
                            **({"pip": True} if window else {}), **({"auto": c.auto} if c.auto else {}), **extra})
    out.details.sort(key=lambda d: (d["start"], d["n"]))
    soften(out.files, float(script_settings(cfg).get("cutaway_fade") or 0), fps, tl.total)
    for w in out.warnings:
        log(f"  ⚠ {w}")
    return out


def _window(cfg: dict, tl: Timeline, c: Cutaway, W: int, H: int, infos: list[dict] | None,
            analyses: list[dict] | None) -> dict | None:
    """The presenter's window for a cutaway (domain/pip.layout), cropped around the face of the shot
    that is playing when the cutaway starts. None when graphics cannot draw its frame."""
    face = None
    clip = next((k for k in tl if k.out_start <= c.start < k.out_end and k.piece.source_window() is not None), None)
    if clip is not None and infos and analyses and clip.piece.source < len(analyses):
        info, framing = infos[clip.piece.source], cfg.get("framing") or {}
        focus = (float(clip.data.get("focus_x", framing.get("focus_x", 0.5))),
                 float(clip.data.get("focus_y", framing.get("focus_y", 0.5))))
        face = pip_window.face_in_frame((analyses[clip.piece.source] or {}).get("face"), info["width"], info["height"],
                                        W, H, focus, str(framing.get("mode", "auto")))
    return pip_window.layout(W, H, cfg, face)


def _path(svc: Services, project: Project, signature: dict, suffix: str = ".mkv") -> Path:
    key = json.dumps(signature, sort_keys=True, default=str)
    return svc.cache.file(project.cache, f"segments/cut_{hashlib.sha1(key.encode()).hexdigest()[:14]}{suffix}")


def _graphic(svc: Services, project: Project, cfg: dict, c: Cutaway, n: int, W: int, H: int, fps: int,
             theme: dict, draft: bool, out: RenderedCutaways, sound_on: bool, base: float) -> Path:
    spec = {**c.spec, "dur": n / fps}
    scene = build_scene(svc, cfg, spec, W, H, theme)
    if sound_on:
        out.sounds += [(name, c.start + at, base - 2) for name, at in scene.sounds() if at < n / fps]
    path = _path(svc, project, {"spec": spec, "theme": theme, "W": W, "H": H, "fps": fps, "overlay": c.overlay,
                                "language": cfg.get("language"), "draft": draft, "v": CUTAWAY_VERSION},
                 ".mov" if c.overlay else ".mkv")
    if svc.cache.has(path):
        return path
    if c.overlay:           # transparent: only the graphic covers the picture
        frames = (f.tobytes() for f in svc.graphics.overlay_frames(scene, fps, n))
        return svc.encoder.encode_overlay(frames, n, W, H, fps, path)
    return svc.encoder.encode_frames(svc.graphics.frames(scene, fps, n), n, W, H, fps, path, draft=draft)


def footage_records(svc: Services, project: Project) -> dict[str, dict]:
    """footage/credits.json as {file (relative to the project): record}."""
    data = svc.projects.read_json(project.dir / footage.FOLDER / footage.RECORD)
    return {str(r["file"]): r for r in data or [] if isinstance(r, dict) and r.get("file")}


def _footage_detail(svc: Services, project: Project, records: dict[str, dict], file: str, out: RenderedCutaways) -> dict:
    """What the summary says about a clip cutaway: its file and, when it is third-party footage (a file
    inside <project>/footage/), where it comes from and whether its record still matches the file."""
    path = Path(file).expanduser()
    if path.is_absolute() or path.parts[:1] != (footage.FOLDER,):
        return {"file": file}                              # the user's own clip
    rec = records.get(path.as_posix())
    recorded = rec is not None and (not rec.get("sha256") or rec["sha256"] == svc.projects.file_hash(project.dir / path))
    if recorded and rec not in out.credits:
        out.credits.append(rec)
    return {"file": file, "third_party": True, "recorded": recorded,
            **({"source": rec.get("source"), "kind": rec.get("kind", footage.STOCK)} if rec else {})}


def _clip(svc: Services, project: Project, cfg: dict, c: Cutaway, n: int, W: int, H: int, fps: int,
          draft: bool) -> tuple[Path, bool]:
    """A video clip as a silent piece of the frame's size, from `clip_start` (seconds of the clip).
    A clip too short for its time on screen is slowed down (to CLIP_MIN_SPEED at most).
    → (segment, whether it still ends early)."""
    name = str(c.spec.get("file") or "")
    clip = Path(name).expanduser()
    if not clip.is_absolute():
        clip = project.dir / clip
    if not svc.projects.exists(clip):
        raise PlanError(f"clip not found: {clip} (file= is relative to the project folder; `sve broll "
                        f"{project.name}` fetches it again)")
    info = svc.probe.probe(clip)
    length = float(info.get("duration") or 0)
    if length <= 0 or not info.get("width"):
        raise PlanError(f"not a readable video: {clip}")
    dur = n / fps
    start = min(max(float(c.spec.get("clip_start") or 0), 0.0), max(length - 0.1, 0.0))
    speed = float(c.spec.get("speed") or 1.0)
    if speed <= 0:
        raise PlanError("speed must be above 0")
    if length - start < dur * speed - 0.02:               # not enough clip left: play it slower, then start earlier
        speed = max((length - start) / dur, min(speed, CLIP_MIN_SPEED))
        if length - start < dur * speed - 0.02:
            start = max(length - dur * speed, 0.0)
    short = length - start < dur * speed - 0.05
    b = footage.settings(cfg)
    mode = "blur" if c.spec.get("fit") else str(c.spec.get("framing") or b["framing"])    # fit: shown whole over a blurred copy
    framing = {**(cfg.get("framing") or {}), "mode": mode}
    if framing["mode"] not in ("crop", "blur", "auto"):
        raise PlanError(f"unknown framing '{framing['mode']}'. Options: crop, blur, auto")
    shot = {**{k: c.spec[k] for k in CLIP_KEYS if k in c.spec}, "start": round(start, 3),
            "end": round(min(start + dur * speed, length), 3), "speed": round(speed, 4), "dur": dur, "index": c.index,
            "mute": True, "effects": []}
    clip_cfg = {**cfg, "framing": framing}
    path = _path(svc, project, {"shot": shot, "clip": svc.probe.fingerprint(clip), "W": W, "H": H, "fps": fps,
                                "cfg": {k: clip_cfg.get(k) for k in ("color", "framing")}, "draft": draft,
                                "v": CUTAWAY_VERSION})
    if svc.cache.has(path):
        return path, short
    times = np.linspace(shot["start"] + 0.05, max(shot["end"] - 0.05, shot["start"] + 0.06), 6)
    stats = color_math.measure(svc.video.read_frames(str(clip), times))
    return svc.encoder.encode_shot(shot, info, clip_cfg, stats, False, W, H, draft, path), short


def _photo(svc: Services, project: Project, cfg: dict, c: Cutaway, dur: float, W: int, H: int, fps: int,
           draft: bool) -> Path:
    name = str(c.spec.get("file") or "")
    image = Path(name).expanduser()
    if not image.is_absolute():
        image = project.dir / image
    if svc.photos is None or not name or not svc.photos.is_image(image) or not svc.projects.exists(image):
        raise PlanError(f"not a readable photo: {image} (file= is relative to the project folder)")
    motion = str(c.spec.get("motion") or PHOTO_MOTION)
    if motion not in ALL_MOTIONS:
        raise PlanError(f"unknown motion '{motion}'. Options: {', '.join(ALL_MOTIONS)}")
    shot = {**{k: c.spec[k] for k in ("focus_x", "focus_y", "framing", "color", "auto_tone") if k in c.spec},
            "dur": dur, "motion": motion,
            "motion_amount": float(c.spec.get("motion_amount", photo_settings(cfg)["motion_amount"]))}
    path = _path(svc, project, {
        "shot": shot, "image": svc.probe.fingerprint(image), "W": W, "H": H, "fps": fps,
        "cfg": {"color": {k: cfg.get("color", {}).get(k) for k in ("warmth", "vignette")},
                "framing": cfg.get("framing"), "auto_tone": auto_tone_setting(cfg, shot), "crf": photo_crf(cfg)},
        "draft": draft, "v": CUTAWAY_VERSION})
    if svc.cache.has(path):
        return path
    return svc.photos.encode_photo(image, shot, cfg, W, H, fps, path, draft=draft)
