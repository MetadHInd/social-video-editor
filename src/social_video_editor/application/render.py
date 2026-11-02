"""Use case: render plan.json into the final video.

Steps:
  1. Every shot is rendered on its own (cache/segments/, keyed by a fingerprint: unchanged shots are
     not rendered again). Video shots come from the ORIGINAL source (HDR→SDR, framing, color, zoom,
     speed, clean audio); drawn shots (polaroid, collage) from the segments they depend on, so the
     render order follows the dependencies (Sequence.render_order) and dispatch is polymorphic
     (ShotRenderer is a PieceVisitor).
  2. Segments are joined with their transitions; text layer, music and sound effects are added.
  3. Audio is mastered to −14 LUFS; video, cover, .srt, plan_used/config_used/summary.json are
     written to a new version folder (vN, or a variant such as v3SinMusica / v3NoMusic).
     The same FFmpeg pass also writes vN/stems/{original,music,sfx}.wav (pre-master levels; the
     summary's audio.master_gain_db brings them to the final level) and the summary records where
     every text box was drawn (text_boxes) and the typing bursts, for `sve check`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from ..domain import color_math
from ..domain.config import EXPORT_PROFILES, labels, output_name, variant_suffix
from ..domain.models import Project
from ..domain.pieces import CollageShot, PolaroidShot, Sequence, VideoShot
from ..domain.selection import has_voice, resolve_ranges
from ..domain.text_layout import srt_text, subtitle_groups, text_animation
from ..domain.timebase import mmss
from ..domain.check import typing_bursts
from ..domain.timeline import Timeline, absolute_texts, compute_timeline
from ..domain.transitions import get_transition
from ..log import log
from .analyze import prepare_sources
from .music import prepare_music
from .services import Services

SEGMENT_VERSION = 5
SHOT_SIGNATURE_KEYS = ("source", "start", "end", "speed", "effects", "zoom", "focus_x", "focus_y", "volume_db",
                       "mute", "freeze", "color", "_beats")


class ShotRenderer:
    """Renders each shot to a segment file (a PieceVisitor: one method per kind of shot)."""

    def __init__(self, svc: Services, cfg: dict, infos: list[dict], analyses: list[dict], timeline: Timeline,
                 W: int, H: int, draft: bool, cache: Path):
        self.svc, self.cfg, self.infos, self.analyses, self.tl = svc, cfg, infos, analyses, timeline
        self.W, self.H, self.draft, self.cache = W, H, draft, cache
        self.fps = int(cfg["output"]["fps"])
        self.segments: list[Path | None] = [None] * len(timeline)

    def render(self, order: list[int]) -> list[Path]:
        for i in order:
            self.segments[i] = self.tl[i].piece.accept(self)
        return self.segments

    def _header(self, shot) -> str:
        return f"  shot {shot.index + 1}/{len(self.tl)}:"

    def _segment_path(self, signature: dict, prefix: str = "", n: int = 16) -> Path:
        key = json.dumps(signature, sort_keys=True, default=str)
        path = self.cache / "segments" / f"{prefix}{hashlib.sha1(key.encode()).hexdigest()[:n]}.mkv"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # ── video shots ──────────────────────────────────────────────────────────
    def visit_video(self, shot: VideoShot) -> Path:
        d = self.tl[shot.index].as_dict()
        log(f"{self._header(shot)} source {shot.source} {mmss(d['start'])}→{mmss(d['end'])} "
            f"x{d.get('speed', 1)} [{d.get('transition')}] {d.get('role', '')}")
        info, an = self.infos[shot.source], self.analyses[shot.source]
        path = self._segment_path({
            "shot": {k: d.get(k) for k in SHOT_SIGNATURE_KEYS}, "parity": shot.index % 2, "dur": d["dur"],
            "src": info["path"], "W": self.W, "H": self.H,
            "cfg": {k: self.cfg.get(k) for k in ("color", "framing", "audio", "effects")},
            "draft": self.draft, "v": SEGMENT_VERSION})
        if path.exists():
            return path
        times = np.linspace(d["start"] + 0.05, max(d["end"] - 0.05, d["start"] + 0.06), 6)
        stats = color_math.measure(self.svc.video.read_frames(info["proxy"], times))
        return self.svc.encoder.encode_shot(d, info, self.cfg, stats, has_voice(an, d["start"], d["end"]),
                                            self.W, self.H, self.draft, path)

    # ── drawn shots ──────────────────────────────────────────────────────────
    def _drawn_path(self, shot, refs: list[int]) -> tuple[dict, Path]:
        d = self.tl[shot.index].as_dict()
        log(f"{self._header(shot)} {shot.kind} ({d['dur']:.2f} s) [{d.get('transition')}]")
        path = self._segment_path({
            "shot": {k: v for k, v in d.items() if k not in ("out_start", "out_end", "index", "overlap")},
            "refs": [str(self.segments[k]) for k in refs], "W": self.W, "H": self.H,
            "cfg": {k: self.cfg.get(k) for k in ("polaroid", "collage")}, "v": SEGMENT_VERSION},
            prefix=f"{shot.kind}_", n=14)
        return d, path

    def visit_polaroid(self, shot: PolaroidShot) -> Path:
        d, path = self._drawn_path(shot, [shot.from_shot])
        if path.exists():
            return path
        png = self.svc.encoder.extract_frame(self.segments[shot.from_shot], path.with_suffix(".last.png"))
        try:
            frames = self.svc.composer.polaroid_frames(png, d, self.W, self.H, self.fps, self.cfg)
            return self.svc.encoder.encode_frames(frames, int(round(d["dur"] * self.fps)), self.W, self.H,
                                                  self.fps, path)
        finally:
            png.unlink(missing_ok=True)

    def visit_collage(self, shot: CollageShot) -> Path:
        d, path = self._drawn_path(shot, shot.from_shots + [shot.background_from])
        if path.exists():
            return path
        pngs = []
        try:
            for j, k in enumerate(shot.from_shots):         # the frame at 55 % of each shot
                seg = self.segments[k]
                pngs.append(self.svc.encoder.extract_frame(seg, path.with_name(f"{path.stem}_{j}.png"),
                                                           at=self.svc.probe.duration(seg) * 0.55))
            bg = self.svc.encoder.extract_frame(self.segments[shot.background_from],
                                                path.with_name(f"{path.stem}_bg.png"))
            pngs.append(bg)
            times = shot.appearance_times(d["dur"], int(self.cfg.get("collage", {}).get("subdivision", 2)))
            frames = self.svc.composer.collage_frames(pngs[:-1], bg, d, times, self.W, self.H, self.fps, self.cfg)
            return self.svc.encoder.encode_frames(frames, int(round(d["dur"] * self.fps)), self.W, self.H,
                                                  self.fps, path)
        finally:
            for p in pngs:
                p.unlink(missing_ok=True)


def sound_events(timeline: Timeline, texts: list[dict], keystrokes: list[float], cfg: dict) -> list:
    """Every sound effect of the video: (name, second, gain dB)."""
    sx = cfg["sfx"]
    base = float(sx.get("volume_db", -12))
    events = []
    if not sx.get("enabled", True):
        return events
    for clip in timeline.clips[1:]:
        snd = get_transition(clip.piece.transition).sound
        if snd and sx.get("whoosh_on_transitions", True):
            events.append((snd, max(clip.out_start - (0.12 if snd == "whoosh" else 0), 0), base))
    for tx in texts:
        anim = text_animation(tx, cfg["texts"]["styles"])
        if sx.get("pop_on_texts", True) and anim == "pop" and tx.get("sound", True):
            events.append(("pop", tx["start"], base - 2))
        if tx.get("sfx"):
            events.append((tx["sfx"], tx["start"], base))
    if sx.get("typing", True):
        events += [("keystroke", t, base + float(sx.get("typing_db", -9))) for t in keystrokes]
    for clip in timeline:
        events += clip.piece.sound_events(clip.out_start, clip.dur, base, cfg)
    return events


def _master_gain(lufs: dict) -> float | None:
    """Gain the mastering applied (≈ after − before LUFS): brings the pre-master stems to the final level."""
    try:
        return round(float(lufs["after_lufs"]) - float(lufs["before_lufs"]), 2)
    except (KeyError, TypeError, ValueError):
        return None


def prepare_edit(svc: Services, project: Project, plan: dict, cfg: dict, no_music: bool = False):
    """What render does before encoding: resolve "search" windows, music + beat alignment, timeline.

    Returns (plan, infos, analyses, music, timeline); plan and cfg are modified in place.
    """
    infos, analyses = prepare_sources(svc, project, cfg)
    resolve_ranges(plan, analyses)
    for s in plan["shots"]:           # resolved: plan_used.json keeps the final times
        if "search" in s:
            s["_searched"] = s.pop("search")
    music = prepare_music(svc, project, plan, cfg, infos, no_music)
    return plan, infos, analyses, music, compute_timeline(Sequence.from_plan(plan), cfg)


def render(svc: Services, project: Project, plan: dict, draft: bool = False, profile: str | None = None,
           no_music: bool = False) -> Path:
    t0 = time.time()
    cfg = copy.deepcopy(project.cfg)
    if profile:
        p = EXPORT_PROFILES[profile]
        cfg["output"].update(width=p["width"], height=p["height"])
        cfg["export"].update(crf=p["crf"], maxrate=p["maxrate"])
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    if draft:
        W, H = W // 2 // 2 * 2, H // 2 // 2 * 2
    plan, infos, analyses, music, tl = prepare_edit(svc, project, copy.deepcopy(plan), cfg, no_music)
    order = tl.sequence.render_order()               # validates shot references before encoding anything
    total = tl.total
    log(f"Timeline: {len(tl)} shots, {total:.1f} s, {W}×{H}{' (draft)' if draft else ''}")

    segments = ShotRenderer(svc, cfg, infos, analyses, tl, W, H, draft, project.cache).render(order)

    # Main version = new number (v4). Variants = number of the latest main version + suffix
    # (v4SinMusica / v4NoMusic, v4Borrador / v4Draft, v4Youtube…), so you can see where they come from.
    lang = cfg["language"]
    suffix = variant_suffix(lang, draft, profile, no_music)
    vdir = svc.projects.variant_folder(project, suffix) if suffix else svc.projects.new_version(project)
    name = output_name(project.name, lang, draft, profile, no_music)
    texts = absolute_texts(plan, tl)
    groups = subtitle_groups(analyses, tl, cfg) if cfg["subtitles"].get("auto", True) else []
    boxes: list[dict] = []
    layer, keystrokes = svc.text.render_layer(texts, groups, cfg, W, H, total, vdir / "_layer", boxes)
    events = sound_events(tl, texts, keystrokes, cfg)
    sfx = svc.sfx.mix_track(events, total, vdir / "_sfx.wav") if events else None

    voice = any(has_voice(analyses[w[0]], w[1], w[2])
                for w in (c.piece.source_window() for c in tl) if w is not None)
    premaster = vdir / f"_{name}_premaster.mkv"
    final = vdir / f"{name}.mp4"
    try:
        svc.encoder.compose([{"dur": c.dur, "overlap": c.overlap, "transition": c.piece.transition} for c in tl],
                            segments, layer, music, sfx, cfg, W, H, total, voice, draft, vdir, premaster,
                            stems_dir=vdir / "stems")
        lufs = svc.encoder.master_audio(premaster, final, cfg)
    except Exception:
        failed = svc.projects.mark_failed(vdir, labels(lang)["failed"])
        log(f"Render failed; diagnostic files in {failed}")
        raise
    for tmp in (premaster, vdir / "_sfx.wav", vdir / "_layer"):     # temporary files
        svc.projects.remove(tmp)

    # Extras
    svc.projects.write_text(vdir / f"{name}.srt", srt_text(groups, texts))
    if cfg["export"].get("cover", True):
        t_cover = min(float(cfg["export"].get("cover_at", 1.6)), total / 2)
        svc.encoder.cover(final, t_cover, vdir / f"{name}{labels(lang)['cover']}.jpg")
    svc.projects.write_json(vdir / "plan_used.json", plan)
    svc.projects.write_json(vdir / "config_used.json", cfg)
    summary = {
        "video": final.name, "duration_sec": round(total, 2), "shots": len(tl), "resolution": f"{W}x{H}",
        "music": None if not music else {"file": music.name, "start_sec": cfg["music"].get("_start")},
        "audio": {**lufs, "master_gain_db": _master_gain(lufs)}, "auto_subtitles": len(groups),
        "texts": len(texts), "sound_effects": len(events),
        "render_time_sec": round(time.time() - t0, 1),
        "shot_details": [{"n": c.index + 1, **c.piece.summary(),
                          "in_video": f"{mmss(c.out_start)}–{mmss(c.out_end)}",
                          "cut_sec": round(tl.cut(c.index), 3),
                          "role": c.data.get("role"), "transition": c.data.get("transition")} for c in tl],
        "stems": {k: f"stems/{k}.wav" for k in ("original", "music", "sfx") if (vdir / "stems" / f"{k}.wav").exists()},
        "text_boxes": boxes,
        "typing": typing_bursts(keystrokes),
    }
    svc.projects.write_json(vdir / "summary.json", summary)
    log(f"Done → {final}  ({total:.1f} s, {lufs.get('after_lufs', '?')} LUFS, "
        f"{summary['render_time_sec']} s of rendering)")
    return final
