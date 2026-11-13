"""Use case: render plan.json into the final video.

Steps:
  1. Every shot is rendered on its own (cache/segments/, keyed by a fingerprint: unchanged shots are
     not rendered again). Video shots come from the ORIGINAL source (HDR→SDR, framing, color, zoom,
     speed, clean audio); drawn shots (polaroid, collage) from the segments they depend on, so the
     render order follows the dependencies (Sequence.render_waves) and dispatch is polymorphic
     (ShotRenderer is a PieceVisitor). Up to render.jobs ($SVE_JOBS) video / photo shots of a wave are
     encoded at the same time; the result does not depend on the number of jobs.
  2. Segments are joined with their transitions; the plan's cutaways (graphics / photos shown over the
     main track while its sound goes on, application/cutaways.py) are laid over them; text layer, music
     and sound effects are added.
  3. Audio is mastered to −14 LUFS; video, cover, .srt, plan_used/config_used/summary.json are
     written to a new version folder (vN, or a variant such as v3SinMusica / v3NoMusic).
     The same FFmpeg pass also writes vN/stems/{original,music,sfx}.wav (pre-master levels; the
     summary's audio.master_gain_db brings them to the final level; skipped for long renders and
     renders without original sound unless export.stems: true, see stems_wanted) and the summary
     records where every text box was drawn (text_boxes) and the typing bursts, for `sve check`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from ..domain import color_math
from ..domain.config import EXPORT_PROFILES, labels, output_name, variant_suffix
from ..domain.explainer import refocus_faces, subtitle_analyses
from ..domain.footage import CREDITS_FILE, credits_text
from ..domain.models import Project, UserError
from ..domain.models import PlanError
from ..domain.photos import auto_tone_setting, photo_crf, photo_settings, resolve_photo_ref
from ..domain.pieces import CollageShot, GraphicShot, PhotoShot, PolaroidShot, Sequence, VideoShot
from ..domain.selection import has_voice, resolve_ranges
from ..domain.text_layout import srt_text, subtitle_groups, text_animation
from ..domain.timebase import mmss
from ..domain.check import thin_keystrokes, typing_bursts
from ..domain.timeline import Timeline, absolute_texts, compute_timeline
from ..domain.transitions import get_transition
from ..log import log
from .analyze import prepare_sources
from .cutaways import render_cutaways
from .enhance import enhanced_or_none
from .graphic import build_scene, graphics_theme
from .music import prepare_music
from .products import visual_search
from .services import Services

SEGMENT_VERSION = 7
CLEAN_VOICE_VERSION = 1     # change to clean every recording again (adapters/ffmpeg/voice.py)
SOURCE_COLOR_FRAMES = 24      # frames measured across a whole recording (color.per_source)
STEMS_MAX_SEC = 600         # longer renders write no stems by default (≈ 0.7 GB per stem at 60 min)
SHOT_SIGNATURE_KEYS = ("source", "start", "end", "speed", "effects", "zoom", "focus_x", "focus_y", "volume_db",
                       "mute", "freeze", "color", "_beats")
# Shots that may be encoded in worker threads: each one is an FFmpeg process plus thread-safe work
# (OpenCV, numpy, Pillow on its own image). Drawn shots stay on the calling thread: they draw text
# with shared Pillow FreeType font objects, which are not thread-safe.
POOLED_SHOTS = (VideoShot, PhotoShot)


class ShotRenderer:
    """Renders each shot to a segment file (a PieceVisitor: one method per kind of shot).

    jobs > 1: the video / photo shots of each dependency wave are encoded by that many worker threads
    (each runs its own FFmpeg process); drawn shots are rendered on the calling thread. jobs = 1 renders
    every shot on the calling thread in Sequence.render_order(), exactly like a serial render.
    """

    def __init__(self, svc: Services, cfg: dict, infos: list[dict], analyses: list[dict], timeline: Timeline,
                 W: int, H: int, draft: bool, cache: Path, jobs: int = 1):
        self.svc, self.cfg, self.infos, self.analyses, self.tl = svc, cfg, infos, analyses, timeline
        self.W, self.H, self.draft, self.cache = W, H, draft, cache
        self.fps = int(cfg["output"]["fps"])
        self.jobs = max(1, int(jobs))
        self.segments: list[Path | None] = [None] * len(timeline)
        self._claims: dict[Path, threading.Lock] = {}
        self._source_stats: dict[int, dict] = {}       # color.per_source: one measurement per recording
        self._claims_guard = threading.Lock()
        self._stop = threading.Event()                # set when a pooled shot fails: the pending ones are skipped
        self._theme: dict | None = None               # graphics theme, built on the first graphic shot

    def render(self, waves: list[list[int]]) -> list[Path]:
        """Renders the shots wave by wave (Sequence.render_waves: a wave only needs earlier waves).

        In a pool, the first shot that fails cancels the shots not started yet; the running ones are
        waited for and that first exception is re-raised as is."""
        for wave in waves:
            pool = [i for i in wave if isinstance(self.tl[i].piece, POOLED_SHOTS)] if self.jobs > 1 else []
            if len(pool) < 2:
                pool = []
            else:
                self._render_pool(pool)
            pooled = set(pool)
            for i in wave:
                if i not in pooled:
                    self.segments[i] = self.tl[i].piece.accept(self)
        return self.segments

    def _render_pool(self, pool: list[int]) -> None:
        workers = min(self.jobs, len(pool))
        log(f"  rendering {len(pool)} shots, {workers} at a time")
        self._stop.clear()
        ex = ThreadPoolExecutor(workers, thread_name_prefix="sve-shot")
        try:
            futures = {ex.submit(self._pooled, i): i for i in pool}                 # started in story order
            for fut in as_completed(futures):
                self.segments[futures[fut]] = fut.result()                          # raises the shot's error
        finally:
            self._stop.set()           # also on Ctrl-C: a worker that is free now starts nothing new
            ex.shutdown(wait=True, cancel_futures=True)

    def _pooled(self, i: int) -> Path | None:
        """One shot in a worker thread. After a failure, shots a worker picks up before the pool is shut
        down are skipped (cancel_futures only cancels the ones no worker has taken yet)."""
        if self._stop.is_set():
            return None
        try:
            return self.tl[i].piece.accept(self)
        except BaseException:
            self._stop.set()
            raise

    @contextmanager
    def _claim(self, path: Path) -> Iterator[None]:
        """Only one thread at a time renders a given segment file: two identical shots (same signature,
        same path) must not encode the same temporary file at once; the second one finds it cached."""
        with self._claims_guard:
            lock = self._claims.setdefault(path, threading.Lock())
        with lock:
            yield

    def _header(self, shot) -> str:
        return f"  shot {shot.index + 1}/{len(self.tl)}:"

    def _segment_path(self, signature: dict, prefix: str = "", n: int = 16) -> Path:
        key = json.dumps(signature, sort_keys=True, default=str)
        return self.svc.cache.file(self.cache, f"segments/{prefix}{hashlib.sha1(key.encode()).hexdigest()[:n]}.mkv")

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
        with self._claim(path):
            if self.svc.cache.has(path):
                return path
            stats = self._color_stats(shot.source, info, d)
            clean = self._clean_voice(info)
            return self.svc.encoder.encode_shot(d, {**info, "clean_audio": clean} if clean else info, self.cfg, stats,
                                                has_voice(an, d["start"], d["end"]), self.W, self.H, self.draft, path)

    def _clean_voice(self, info: dict) -> Path | None:
        """audio.clean_voice: the recording's whole audio cleaned once (cached), so every shot takes its
        piece of the same clean track and the noise reduction never restarts at a cut."""
        a = self.cfg.get("audio") or {}
        if not a.get("clean_voice") or not info.get("has_audio") or not a.get("use_original", True):
            return None
        mix, highpass = float(a.get("clean_mix", 1.0)), int(a.get("highpass") or 0)
        key = hashlib.sha1(json.dumps([info["path"], info.get("id"), mix, highpass, CLEAN_VOICE_VERSION]).encode())
        path = self.svc.cache.file(self.cache, f"voice/{info.get('id', 'source')}_{key.hexdigest()[:10]}.wav")
        with self._claim(path):
            if not self.svc.cache.has(path):
                log(f"Voice: cleaning the background noise of {Path(info['path']).name} (once, whole recording)…")
                self.svc.encoder.clean_voice(info, path, mix, highpass)
        return path

    def _color_stats(self, source: int, info: dict, d: dict) -> dict:
        """Color statistics the shot is corrected from: its own frames, or with color.per_source the
        whole recording's (one correction for all its shots, so the light does not jump at the cuts)."""
        if not (self.cfg.get("color") or {}).get("per_source"):
            times = np.linspace(d["start"] + 0.05, max(d["end"] - 0.05, d["start"] + 0.06), 6)
            return color_math.measure(self.svc.video.read_frames(info["proxy"], times))
        if source not in self._source_stats:
            length = float(info.get("duration") or d["end"])
            times = np.linspace(min(0.5, length / 4), max(length - 0.5, length * 3 / 4), SOURCE_COLOR_FRAMES)
            self._source_stats[source] = color_math.measure(self.svc.video.read_frames(info["proxy"], times))
        return self._source_stats[source]

    # ── drawn shots ──────────────────────────────────────────────────────────
    def _drawn_path(self, shot, refs: list[int], audio: dict | None = None) -> tuple[dict, Path]:
        d = self.tl[shot.index].as_dict()
        log(f"{self._header(shot)} {shot.kind} ({d['dur']:.2f} s) [{d.get('transition')}]")
        sig_audio = audio and {k: audio[k] for k in ("start", "shot", "voice", "gain_db", "fade")}
        path = self._segment_path({
            "shot": {k: v for k, v in d.items() if k not in ("out_start", "out_end", "index", "overlap")},
            "refs": [str(self.segments[k]) for k in refs], "W": self.W, "H": self.H,
            "cfg": {k: self.cfg.get(k) for k in ("polaroid", "collage")}, "draft": self.draft, "v": SEGMENT_VERSION,
            "audio": sig_audio and {**sig_audio, "src": audio["info"]["path"], "cfg": self.cfg.get("audio")}},
            prefix=f"{shot.kind}_", n=14)
        return d, path

    def _continuation_audio(self, ref: int, dur: float) -> dict | None:
        """Audio for a drawn shot: the source sound that continues after shot `ref` ends, through the
        same per-shot chain, audio.drawn_shot_db quieter (default −6 dB) with short fades — so the
        sound does not drop to silence under a polaroid / collage. None → silent (no source audio)."""
        window = self.tl[ref].piece.source_window()
        if window is None:
            return None
        source, _, end, speed = window
        info = self.infos[source]
        if not info.get("has_audio") or float(info.get("duration", 0)) - end < 0.1:
            return None
        ref_data = self.tl[ref].data
        return {"info": info, "start": round(end, 3),
                "shot": {"speed": speed, "volume_db": ref_data.get("volume_db", 0), "mute": ref_data.get("mute")},
                "voice": has_voice(self.analyses[source], end, end + dur * speed), "cfg": self.cfg,
                "gain_db": float(self.cfg["audio"].get("drawn_shot_db", -6)), "fade": 0.12}

    def visit_polaroid(self, shot: PolaroidShot) -> Path:
        audio = self._continuation_audio(shot.from_shot, self.tl[shot.index].dur)
        d, path = self._drawn_path(shot, [shot.from_shot], audio)
        if self.svc.cache.has(path):
            return path
        png = self.svc.encoder.extract_frame(self.segments[shot.from_shot], _temp_png(path, "last"))
        try:
            frames = self.svc.composer.polaroid_frames(png, d, self.W, self.H, self.fps, self.cfg)
            return self.svc.encoder.encode_frames(frames, int(round(d["dur"] * self.fps)), self.W, self.H,
                                                  self.fps, path, audio, draft=self.draft)
        finally:
            self.svc.cache.discard(png)

    def visit_collage(self, shot: CollageShot) -> Path:
        audio = self._continuation_audio(shot.background_from, self.tl[shot.index].dur)
        d, path = self._drawn_path(shot, shot.from_shots + [shot.background_from], audio)
        if self.svc.cache.has(path):
            return path
        pngs = []
        try:
            for j, k in enumerate(shot.from_shots):         # the frame at 55 % of each shot
                seg = self.segments[k]
                pngs.append(self.svc.encoder.extract_frame(seg, _temp_png(path, str(j)),
                                                           at=self.svc.probe.duration(seg) * 0.55))
            bg = self.svc.encoder.extract_frame(self.segments[shot.background_from], _temp_png(path, "bg"))
            pngs.append(bg)
            times = shot.appearance_times(d["dur"], int(self.cfg.get("collage", {}).get("subdivision", 2)))
            frames = self.svc.composer.collage_frames(pngs[:-1], bg, d, times, self.W, self.H, self.fps, self.cfg)
            return self.svc.encoder.encode_frames(frames, int(round(d["dur"] * self.fps)), self.W, self.H,
                                                  self.fps, path, audio, draft=self.draft)
        finally:
            for p in pngs:
                self.svc.cache.discard(p)

    # ── graphics ─────────────────────────────────────────────────────────────
    def visit_graphic(self, shot: GraphicShot) -> Path:
        """An animated graphic drawn from its data (domain/graphics). Silent; its sound effects are
        recorded on the shot ("_sounds") for the effects track."""
        d = self.tl[shot.index].as_dict()
        if self._theme is None:
            self._theme = graphics_theme(self.svc, self.cfg)
        spec = {**shot.spec(), "dur": d["dur"]}
        try:
            scene = build_scene(self.svc, self.cfg, spec, self.W, self.H, self._theme)
        except PlanError as e:
            raise PlanError(f"Shot {shot.index} (graphic {shot.template}): {e}") from None
        shot.data["_sounds"] = [[name, round(at, 3)] for name, at in scene.sounds()]
        log(f"{self._header(shot)} graphic {shot.template} ({d['dur']:.2f} s) [{d.get('transition')}]")
        path = self._segment_path({"spec": spec, "theme": self._theme, "W": self.W, "H": self.H, "fps": self.fps,
                                   "language": self.cfg.get("language"), "draft": self.draft, "v": SEGMENT_VERSION},
                                  prefix="graphic_", n=14)
        if self.svc.cache.has(path):
            return path
        n = int(round(d["dur"] * self.fps))
        return self.svc.encoder.encode_frames(self.svc.graphics.frames(scene, self.fps, n), n, self.W, self.H,
                                              self.fps, path, draft=self.draft)

    # ── still photos ─────────────────────────────────────────────────────────
    def visit_photo(self, shot: PhotoShot) -> Path:
        d = self.tl[shot.index].as_dict()
        try:
            photo = resolve_photo_ref(shot.ref, self.infos)
        except ValueError as e:
            raise PlanError(f"Shot {shot.index} (photo): {e}") from None
        if self.svc.photos is None or not self.svc.photos.is_image(photo) or not self.svc.projects.exists(Path(photo)):
            raise PlanError(f"Shot {shot.index} (photo): not a readable photo: {photo}")
        image = enhanced_or_none(self.svc, self.cache, photo) or Path(photo)
        d["motion"] = shot.motion
        d.setdefault("motion_amount", photo_settings(self.cfg)["motion_amount"])
        log(f"{self._header(shot)} photo {Path(photo).name} ({d['dur']:.2f} s, {d['motion']}) "
            f"[{d.get('transition')}]{' enhanced' if image != Path(photo) else ''}")
        path = self._segment_path({
            "shot": {k: d.get(k) for k in ("motion", "motion_amount", "focus_x", "focus_y", "framing", "color", "dur")},
            "image": self.svc.probe.fingerprint(image), "W": self.W, "H": self.H, "fps": self.fps,
            "cfg": {"color": {k: self.cfg.get("color", {}).get(k) for k in ("warmth", "vignette")},
                    "framing": self.cfg.get("framing"), "auto_tone": auto_tone_setting(self.cfg, d),
                    "crf": photo_crf(self.cfg)},
            "draft": self.draft, "v": SEGMENT_VERSION}, prefix="photo_")
        with self._claim(path):
            if self.svc.cache.has(path):
                return path
            return self.svc.photos.encode_photo(image, d, self.cfg, self.W, self.H, self.fps, path, draft=self.draft)


def _temp_png(segment: Path, tag: str) -> Path:
    """A frame extracted for a drawn shot, next to its segment; the thread id keeps two renders of the
    same drawn shot (identical signature) from writing the same file."""
    return segment.with_name(f"{segment.stem}_{tag}_{threading.get_ident()}.png")


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


def stems_wanted(cfg: dict, timeline: Timeline, total: float) -> bool:
    """export.stems: true / false decides; unset (null) → only for renders up to STEMS_MAX_SEC that
    contain original sound (at least one shot playing a source clip; photo-only videos have none)."""
    v = (cfg.get("export") or {}).get("stems")
    if v is not None and v != "auto":
        return bool(v)
    if total > STEMS_MAX_SEC:
        return False
    return any(c.piece.source_window() is not None for c in timeline)


def _master_gain(lufs: dict) -> float | None:
    """Gain the mastering applied (≈ after − before LUFS): brings the pre-master stems to the final level."""
    try:
        return round(float(lufs["after_lufs"]) - float(lufs["before_lufs"]), 2)
    except (KeyError, TypeError, ValueError):
        return None


REFERENCE_FRAMES = 24
REFERENCE_SPAN = (0.04, 0.85)     # skip the very start and the end card a downloaded clip often has


def reference_path(svc: Services, project: Project, value: str) -> Path:
    """A color.reference path: absolute, ~, relative to the project folder or to the workspace."""
    p = Path(value).expanduser()
    for c in ([p] if p.is_absolute() else [project.dir / p, Path.cwd() / p]):
        if svc.probe.fingerprint(c).get("size") is not None:
            return c
    raise UserError(f"color.reference: file not found: {value}")


def apply_color_reference(svc: Services, project: Project, cfg: dict) -> None:
    """With color.reference, measures that video once and stores its look in cfg["color"]["_reference"]."""
    ref = cfg["color"].get("reference")
    if not ref or cfg["color"].get("_reference"):
        return
    path = reference_path(svc, project, str(ref))
    dur = svc.probe.duration(path)
    times = np.linspace(dur * REFERENCE_SPAN[0], dur * REFERENCE_SPAN[1], REFERENCE_FRAMES)
    stats = color_math.measure(svc.video.read_frames(str(path), times))
    if not stats:
        raise UserError(f"color.reference: could not read frames of {path}")
    cfg["color"]["_reference"] = {k: round(v, 4) for k, v in stats.items()}
    log(f"Color: matching the look of {path.name} (brightness {stats['luma']:.2f}, "
        f"blacks {stats['p02']:.2f}, whites {stats['p98']:.2f}, saturation {stats['sat']:.2f})")


def prepare_edit(svc: Services, project: Project, plan: dict, cfg: dict, no_music: bool = False):
    """What render does before encoding: resolve "search" windows, music + beat alignment, timeline.

    Returns (plan, infos, analyses, music, timeline); plan and cfg are modified in place.
    """
    infos, analyses = prepare_sources(svc, project, cfg)
    apply_color_reference(svc, project, cfg)
    resolve_ranges(plan, analyses, visual_search(svc, project, infos, analyses, plan, cfg))
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
    if profile:                                      # the plan framed the faces for the project's own frame shape
        base = project.cfg["output"]
        old, new = (int(base["width"]), int(base["height"])), (int(cfg["output"]["width"]), int(cfg["output"]["height"]))
        moved = old != new and refocus_faces(plan["shots"], infos, analyses, old, new,
                                             str((cfg.get("framing") or {}).get("mode", "auto")))
        if moved:
            log(f"Framing: {moved} shots framed on the face again for {new[0]}×{new[1]}")
    mix_timeline = cfg["music"].pop("_automix_timeline", None)      # playlist: written to vN/automix.json
    waves = tl.sequence.render_waves()               # validates shot references before encoding anything
    jobs = svc.encoder.parallel_jobs(cfg)            # also validates render.jobs / $SVE_JOBS
    total = tl.total
    log(f"Timeline: {len(tl)} shots, {total:.1f} s, {W}×{H}{' (draft)' if draft else ''}")

    segments = ShotRenderer(svc, cfg, infos, analyses, tl, W, H, draft, project.cache, jobs).render(waves)
    cuts = render_cutaways(svc, project, cfg, plan, tl, W, H, draft, infos, analyses)

    # Main version = new number (v4). Variants = number of the latest main version + suffix
    # (v4SinMusica / v4NoMusic, v4Borrador / v4Draft, v4Youtube…), so you can see where they come from.
    lang = cfg["language"]
    suffix = variant_suffix(lang, draft, profile, no_music)
    vdir = svc.projects.variant_folder(project, suffix) if suffix else svc.projects.new_version(project)
    name = output_name(project.name, lang, draft, profile, no_music)
    premaster = vdir / f"_{name}_premaster.mkv"
    final = vdir / f"{name}.mp4"
    boxes: list[dict] = []
    try:                                  # any failure from here on marks the folder vN_failed / vN_fallida
        texts = absolute_texts(plan, tl)
        groups = (subtitle_groups(subtitle_analyses(plan, analyses, cfg), tl, cfg)
                  if cfg["subtitles"].get("auto", True) else [])
        voice = any(has_voice(analyses[w[0]], w[1], w[2])
                    for w in (c.piece.source_window() for c in tl) if w is not None)
        layer, keystrokes = svc.text.render_layer(texts, groups, cfg, W, H, total, vdir / "_layer", boxes)
        # same typing speed on screen, but at most sfx.typing_max_per_sec click SOUNDS per second
        keystrokes = thin_keystrokes(keystrokes, float(cfg["sfx"].get("typing_max_per_sec", 16)))
        events = sound_events(tl, texts, keystrokes, cfg) + cuts.sounds
        sfx = svc.sfx.mix_track(events, total, vdir / "_sfx.wav") if events else None
        svc.encoder.compose([{"dur": c.dur, "overlap": c.overlap, "transition": c.piece.transition} for c in tl],
                            segments, layer, music, sfx, cfg, W, H, total, voice, draft, vdir, premaster,
                            stems_dir=vdir / "stems" if stems_wanted(cfg, tl, total) else None,
                            **({"cutaways": cuts.files} if cuts.files else {}))
        lufs = svc.encoder.master_audio(premaster, final, cfg)
    except BaseException:                 # UserError is a SystemExit; Ctrl-C also leaves the folder marked
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
    if mix_timeline:
        svc.projects.write_json(vdir / "automix.json", mix_timeline)
    summary = {
        "video": final.name, "duration_sec": round(total, 2), "shots": len(tl), "resolution": f"{W}x{H}",
        "music": None if not music else {"file": music.name, "start_sec": cfg["music"].get("_start"),
                                         **({"playlist": [s["file"] for s in cfg["music"]["_automix"]["songs"]]}
                                            if cfg["music"].get("_automix") else {})},
        "audio": {**lufs, "master_gain_db": _master_gain(lufs)}, "auto_subtitles": len(groups),
        "texts": len(texts), "sound_effects": len(events),
        "render_time_sec": round(time.time() - t0, 1),
        "shot_details": [{"n": c.index + 1, **c.piece.summary(),
                          "in_video": f"{mmss(c.out_start)}–{mmss(c.out_end)}",
                          "cut_sec": round(tl.cut(c.index), 3),
                          "role": c.data.get("role"), "transition": c.data.get("transition")} for c in tl],
        "stems": {k: f"stems/{k}.wav" for k in ("original", "music", "sfx") if svc.projects.exists(vdir / "stems" / f"{k}.wav")},
        "text_boxes": boxes,
        "typing": typing_bursts(keystrokes),
    }
    if plan.get("cutaways"):
        summary["cutaways"] = cuts.details
        summary["cutaway_warnings"] = cuts.warnings
    if cuts.credits:                      # third-party footage: who to credit in the video's description
        svc.projects.write_text(vdir / CREDITS_FILE, credits_text(cuts.credits))
        summary["credits"] = CREDITS_FILE
    svc.projects.write_json(vdir / "summary.json", summary)
    log(f"Done → {final}  ({total:.1f} s, {lufs.get('after_lufs', '?')} LUFS, "
        f"{summary['render_time_sec']} s of rendering)")
    return final
