"""Parallel shot rendering: render.jobs / $SVE_JOBS, the wave scheduler of ShotRenderer and the
guarantee that the result does not depend on the number of jobs."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from PIL import Image

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg.encoder import JOBS_ENV, FfmpegEncoder
from social_video_editor.adapters.files.cache import JsonCacheStore
from social_video_editor.application.render import ShotRenderer
from social_video_editor.domain.config import MAX_JOBS, auto_jobs, resolve_jobs
from social_video_editor.domain.models import UserError
from social_video_editor.domain.pieces import Sequence
from social_video_editor.domain.timeline import compute_timeline

TIMEOUT = 10        # seconds a synchronisation point may wait before the test fails (never reached when it works)


# ── render.jobs / $SVE_JOBS ──────────────────────────────────────────────────

@pytest.mark.parametrize("setting,env,cpus,expected", [
    ("auto", None, 8, auto_jobs(8)),
    (None, None, 8, auto_jobs(8)),                  # key missing → auto
    ("AUTO", None, 2, auto_jobs(2)),
    (1, None, 8, 1),
    (3, None, 1, 3),                                # an explicit number is used even on few CPUs
    (16, None, 4, 16),
    (64, None, 4, MAX_JOBS),                        # capped
    ("2", None, 4, 2),
    (1, "4", 8, 4),                                 # the environment wins over the settings
    ("auto", "1", 8, 1),
    (4, "auto", 8, auto_jobs(8)),
    (4, "  ", 8, 4),                                # blank variable = not set
    (4, "", 8, 4),
])
def test_resolve_jobs(setting, env, cpus, expected):
    assert resolve_jobs(setting, env, cpus) == expected


@pytest.mark.parametrize("cpus,expected", [(1, 1), (2, 1), (3, 1), (4, 2), (8, 4), (64, 4)])
def test_auto_jobs_is_half_the_cpus_between_1_and_4(cpus, expected):
    assert auto_jobs(cpus) == expected


@pytest.mark.parametrize("setting,env,source", [
    (0, None, "render.jobs"), (-2, None, "render.jobs"), ("many", None, "render.jobs"), (2.5, None, "render.jobs"),
    (True, None, "render.jobs"), ([2], None, "render.jobs"),
    (2, "0", "SVE_JOBS"), (2, "two", "SVE_JOBS"), (2, "-1", "SVE_JOBS"), ("auto", "1.5", "SVE_JOBS"),
])
def test_resolve_jobs_rejects_garbage(setting, env, source):
    with pytest.raises(UserError, match=source):
        resolve_jobs(setting, env, 4)


def test_encoder_reads_settings_environment_and_cpus(monkeypatch):
    enc = FfmpegEncoder()
    monkeypatch.delenv(JOBS_ENV, raising=False)
    monkeypatch.setattr(FfmpegEncoder, "usable_cpus", staticmethod(lambda: 8))
    assert enc.parallel_jobs({"render": {"jobs": "auto"}}) == auto_jobs(8)
    assert enc.parallel_jobs({}) == auto_jobs(8)
    assert enc.parallel_jobs({"render": {"jobs": 3}}) == 3
    monkeypatch.setenv(JOBS_ENV, "1")
    assert enc.parallel_jobs({"render": {"jobs": 3}}) == 1
    monkeypatch.setenv(JOBS_ENV, "lots")
    with pytest.raises(UserError, match="SVE_JOBS"):
        enc.parallel_jobs({})


def test_usable_cpus_is_a_positive_count():
    n = FfmpegEncoder.usable_cpus()
    assert isinstance(n, int) and n >= 1


def test_base_recipe_defaults_to_auto_jobs_and_keeps_chunk_defaults(cfg, monkeypatch):
    from social_video_editor.adapters.ffmpeg.encoder import CHUNK_DEFAULTS, chunk_setting

    monkeypatch.delenv(JOBS_ENV, raising=False)
    assert (cfg.get("render") or {}).get("jobs") in (None, "auto")
    assert FfmpegEncoder().parallel_jobs(cfg) == auto_jobs(FfmpegEncoder.usable_cpus())
    assert all(chunk_setting(cfg, k) == v for k, v in CHUNK_DEFAULTS.items())


# ── the scheduler, with fake services ────────────────────────────────────────

class FakeEncoder:
    """Writes a stand-in segment file per shot and records which thread rendered what, and when."""

    def __init__(self, on_shot=None):
        self.on_shot = on_shot or (lambda index: None)
        self.events: list[tuple[str, int, str]] = []     # (what, shot index, thread name)
        self.lock = threading.Lock()
        self.calls: dict[str, int] = {}
        self.outputs: dict[int, Path] = {}               # shot index → the file encode_shot wrote

    def _record(self, what: str, index: int) -> None:
        with self.lock:
            self.events.append((what, index, threading.current_thread().name))

    def encode_shot(self, shot, info, cfg, stats, has_voice, W, H, draft, out: Path) -> Path:
        self._record("start", shot["index"])
        with self.lock:
            self.calls[out.name] = self.calls.get(out.name, 0) + 1
            self.outputs[shot["index"]] = out
        self.on_shot(shot["index"])
        out.write_bytes(b"segment")
        self._record("end", shot["index"])
        return out

    def extract_frame(self, video: Path, out_png: Path, at=None) -> Path:
        assert video.exists()
        out_png.write_bytes(b"png")
        return out_png

    def encode_frames(self, frames, n, W, H, fps, out: Path, audio=None, crf=None, draft=False) -> Path:
        list(frames)
        out.write_bytes(b"drawn")
        return out


class FakeComposer:
    def __init__(self, encoder: FakeEncoder):
        self.encoder = encoder

    def polaroid_frames(self, png, shot, W, H, fps, cfg):
        self.encoder._record("drawn", shot["index"])
        return [b""]

    def collage_frames(self, pngs, bg, shot, times, W, H, fps, cfg):
        self.encoder._record("drawn", shot["index"])
        return [b""]


def _renderer(tmp_path: Path, cfg: dict, shots: list[dict], jobs: int, encoder: FakeEncoder, cache: str = "cache"):
    svc = SimpleNamespace(
        encoder=encoder, composer=FakeComposer(encoder), cache=JsonCacheStore(),
        video=SimpleNamespace(read_frames=lambda proxy, times: [np.full((4, 4, 3), 128, np.uint8)]),
        probe=SimpleNamespace(duration=lambda path: 1.0))
    infos = [{"path": str(tmp_path / "clip.mp4"), "proxy": str(tmp_path / "proxy.mp4"), "has_audio": False,
              "duration": 30.0}]
    analyses = [{"voice": {"segments": []}}]
    tl = compute_timeline(Sequence.from_plan({"shots": shots}), cfg)
    return ShotRenderer(svc, cfg, infos, analyses, tl, 64, 112, True, tmp_path / cache, jobs), tl


def _video(start: float) -> dict:
    return {"source": 0, "start": start, "end": start + 1.0}


SHOTS = [_video(0), _video(2), _video(4),
         {"type": "polaroid", "from_shot": 0, "dur": 0.8},
         {"type": "collage", "from_shots": [1, 2], "background_from": 0, "beats": 2}]


def test_jobs_render_video_shots_at_the_same_time_and_drawn_shots_serially(tmp_path, cfg, capsys):
    barrier = threading.Barrier(2, timeout=TIMEOUT)

    def on_shot(index: int) -> None:
        if index in (0, 1):          # only passes if shots 0 and 1 are being encoded at the same moment
            barrier.wait()

    enc = FakeEncoder(on_shot)
    renderer, tl = _renderer(tmp_path, cfg, SHOTS, 2, enc)
    segments = renderer.render(tl.sequence.render_waves())

    starts = {i: t for what, i, t in enc.events if what == "start"}
    assert sorted(starts) == [0, 1, 2]
    assert all(t.startswith("sve-shot") for t in starts.values())
    drawn = [(i, t) for what, i, t in enc.events if what == "drawn"]
    assert drawn == [(3, "MainThread"), (4, "MainThread")]                  # in story order, on the caller
    last_video_end = max(k for k, e in enumerate(enc.events) if e[0] == "end")
    assert all(enc.events.index(("drawn", i, t)) > last_video_end for i, t in drawn)
    assert [p.name.split("_")[0] for p in segments[3:]] == ["polaroid", "collage"]
    assert segments[:3] == [enc.outputs[i] for i in range(3)]               # segment i is shot i's file
    assert len(set(segments)) == 5 and all(p.exists() for p in segments)
    assert "rendering 3 shots, 2 at a time" in capsys.readouterr().out


def test_segments_are_ordered_by_shot_whatever_finishes_first(tmp_path, cfg):
    others_done = threading.Event()
    lock = threading.Lock()
    finished: list[int] = []

    def on_shot(index: int) -> None:
        if index == 0:               # shot 0 finishes last: after shots 1 and 2
            assert others_done.wait(TIMEOUT)
        with lock:
            finished.append(index)
            if len(finished) == 2 and 0 not in finished:
                others_done.set()

    enc = FakeEncoder(on_shot)
    renderer, tl = _renderer(tmp_path, cfg, SHOTS[:3], 2, enc)
    parallel = renderer.render(tl.sequence.render_waves())
    assert finished[-1] == 0
    assert parallel == [enc.outputs[i] for i in range(3)]

    serial_enc = FakeEncoder()
    serial, tl1 = _renderer(tmp_path, cfg, SHOTS[:3], 1, serial_enc, "serial_cache")
    assert [p.name for p in serial.render(tl1.sequence.render_waves())] == [p.name for p in parallel]


def test_one_job_renders_like_before(tmp_path, cfg, capsys):
    enc = FakeEncoder()
    renderer, tl = _renderer(tmp_path, cfg, SHOTS, 1, enc)
    renderer.render(tl.sequence.render_waves())
    assert [(w, i) for w, i, _ in enc.events if w != "end"] == [
        ("start", 0), ("start", 1), ("start", 2), ("drawn", 3), ("drawn", 4)]   # Sequence.render_order()
    assert {t for _, _, t in enc.events} == {"MainThread"}
    assert "at a time" not in capsys.readouterr().out


def test_first_failure_cancels_pending_shots_and_is_reraised(tmp_path, cfg):
    error = RuntimeError("FFmpeg failed on shot 0")
    started_1, finished_1 = threading.Event(), threading.Event()
    renderer = None

    def on_shot(index: int) -> None:
        if index == 0:
            assert started_1.wait(TIMEOUT)
            raise error
        if index == 1:
            started_1.set()
            assert renderer._stop.wait(TIMEOUT)          # still running when shot 0 fails
            finished_1.set()

    enc = FakeEncoder(on_shot)
    renderer, tl = _renderer(tmp_path, cfg, [_video(0), _video(2), _video(4), _video(6), _video(8)], 2, enc)
    with pytest.raises(RuntimeError) as excinfo:
        renderer.render(tl.sequence.render_waves())
    assert excinfo.value is error                                      # the original exception, as is
    assert finished_1.is_set()                                         # running shots are waited for
    assert sorted(i for what, i, _ in enc.events if what == "start") == [0, 1]   # 2, 3, 4 never started
    assert renderer.segments[2:] == [None, None, None]


def test_identical_shots_are_encoded_once(tmp_path, cfg):
    second = threading.Event()
    lock = threading.Lock()
    seen: list[int] = []

    def on_shot(index: int) -> None:
        with lock:
            seen.append(index)
            if len(seen) > 1 and index in (0, 2):
                second.set()
        if index in (0, 2):
            second.wait(0.3)         # without the per-file claim, the twin would encode the same file now

    enc = FakeEncoder(on_shot)
    # shots 0 and 2: same window and the same parity → the same segment file
    renderer, tl = _renderer(tmp_path, cfg, [_video(0), _video(2), _video(0)], 3, enc)
    segments = renderer.render(tl.sequence.render_waves())
    assert segments[0] == segments[2] != segments[1]
    assert enc.calls == {segments[0].name: 1, segments[1].name: 1}


# ── a real render, 1 job vs 2 jobs ───────────────────────────────────────────

def _ff(*args) -> None:
    from social_video_editor.adapters.system.tools import FFMPEG

    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def _framemd5(path: Path) -> list[str]:
    """Decoded video + audio frames (hashes), without the container: Matroska files are not byte-reproducible."""
    from social_video_editor.adapters.system.tools import FFMPEG

    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "framemd5", "-"],
                       capture_output=True, text=True, check=True)
    return [ln for ln in r.stdout.splitlines() if not ln.startswith("#")]


def _draft_project(workspace: Path) -> Path:
    """Video, photo, polaroid and collage shots. No sped-up shot: on FFmpeg 7.1 a speed 1.5 shot of this
    synthetic clip (atempo + afftdn + apad with the push-in zoom) can deadlock even with 2 decoder threads,
    whatever the number of jobs; that is an encoder issue, not what this test is about."""
    sources = workspace / "sources"
    sources.mkdir()
    clip = sources / "clip.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=s=360x640:d=6:r=30", "-f", "lavfi", "-i", "sine=f=330:d=6:sample_rate=48000",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", clip)
    photo = sources / "photo.jpg"
    im = Image.new("RGB", (480, 360), (40, 90, 200))
    im.paste((230, 200, 40), (0, 0, 120, 90))
    im.save(photo, "JPEG", quality=90)
    d = workspace / "projects" / "par"
    d.mkdir(parents=True)
    project = {"name": "par", "recipe": "unboxing", "sources": [{"path": str(clip)}],
               "settings": {"language": "en", "output": {"width": 360, "height": 640},
                            "subtitles": {"auto": False}, "transcription": {"model": "tiny"},
                            "music": {"file": "none"}, "export": {"cover": False}}}
    plan = {"shots": [
        {"source": 0, "start": 0.2, "end": 1.4, "transition": "cut"},
        {"source": 0, "start": 2.0, "end": 3.0, "transition": "fade"},
        {"type": "photo", "source": str(photo), "duration": 1.0, "motion": "zoom_in", "transition": "cut"},
        {"source": 0, "start": 4.0, "end": 5.2, "effects": [{"type": "zoom_punch", "t": 0}], "transition": "cut"},
        {"type": "polaroid", "from_shot": 0, "dur": 0.8, "text": "Photo", "transition": "cut"},
        {"type": "collage", "from_shots": [0, 1, 3], "background_from": 3, "beats": 2, "transition": "cut"}],
        "texts": [{"text": "Hello", "shot": 0, "style": "label"}]}
    (d / "project.yaml").write_text(yaml.safe_dump(project), encoding="utf-8")
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return d


@pytest.mark.integration
@requires_ffmpeg
def test_render_is_identical_with_one_or_two_jobs(workspace, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    from social_video_editor.application.render import render
    from social_video_editor.container import build_services

    svc = build_services()
    folder = _draft_project(workspace)
    results = {}
    for jobs in ("1", "2"):
        monkeypatch.setenv(JOBS_ENV, jobs)
        project = svc.projects.load(folder)
        final = render(svc, project, svc.projects.load_plan(project), draft=True)
        segments = folder / "cache" / "segments"
        kept = folder / "cache" / f"segments_jobs{jobs}"
        segments.rename(kept)                         # the next render must encode every shot again
        summary = json.loads((final.parent / "summary.json").read_text())
        results[jobs] = {"names": sorted(p.name for p in kept.glob("*.mkv")),
                         "frames": {p.name: _framemd5(p) for p in kept.glob("*.mkv")},
                         "shot_details": summary["shot_details"], "shots": summary["shots"],
                         "duration": summary["duration_sec"]}
    one, two = results["1"], results["2"]
    assert len(one["names"]) == 6
    assert [s["type"] for s in one["shot_details"]] == ["video", "video", "photo", "video", "polaroid", "collage"]
    assert one["names"] == two["names"]
    assert one["shot_details"] == two["shot_details"]
    assert (one["shots"], one["duration"]) == (two["shots"], two["duration"])
    for name in one["names"]:
        assert one["frames"][name] == two["frames"][name], name
        assert one["frames"][name]
