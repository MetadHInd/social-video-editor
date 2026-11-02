"""Regression tests for the second review: source cache ids, failed render folder, ffprobe fps,
application file access through ports, group duration."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from social_video_editor.adapters.ffmpeg import probe as probe_mod
from social_video_editor.adapters.ffmpeg.probe import FfprobeMediaProbe, frame_rate
from social_video_editor.adapters.files.cache import JsonCacheStore
from social_video_editor.application import analyze
from social_video_editor.domain.pieces import Group, Sequence
from social_video_editor.domain.timeline import compute_timeline

from conftest import requires_ffmpeg

SRC = Path(__file__).resolve().parent.parent / "src" / "social_video_editor"


# ── 1. source cache ids ──────────────────────────────────────────────────────

class FakeProbe(FfprobeMediaProbe):
    """Real fingerprint (path, size, mtime); fake ffprobe."""

    def __init__(self):
        self.probed = []

    def probe(self, path):
        self.probed.append(str(path))
        return {"path": str(path), "duration": 5.0, "has_audio": False}


class FakeEncoder:
    def make_proxy(self, info, out, hdr_white):
        out.write_bytes(b"proxy")
        return out


class FakeServices:
    def __init__(self):
        self.probe, self.encoder, self.cache = FakeProbe(), FakeEncoder(), JsonCacheStore()


@pytest.fixture
def same_names(tmp_path):
    a, b = tmp_path / "a" / "clip_0001.mov", tmp_path / "b" / "clip_0001.mov"
    for f, data in ((a, b"first clip"), (b, b"another, longer clip")):
        f.parent.mkdir()
        f.write_bytes(data)
    cache = tmp_path / "cache"
    cache.mkdir()
    return a, b, cache


def _prepare(svc, path, cache, cfg):
    return analyze.prepare_source(svc, {"path": str(path)}, cache, cfg)


def test_same_file_name_in_two_folders_gets_two_cache_ids(same_names, cfg):
    a, b, cache = same_names
    svc = FakeServices()
    ia, ib = _prepare(svc, a, cache, cfg), _prepare(svc, b, cache, cfg)
    assert ia["id"] != ib["id"] and ia["id"].startswith("clip_0001_") and ib["id"].startswith("clip_0001_")
    assert ia["path"] == str(a) and ib["path"] == str(b)          # was: b silently got a's info
    assert ia["proxy"] != ib["proxy"] and svc.probe.probed == [str(a), str(b)]
    # stable: the second time everything comes from the cache
    assert _prepare(svc, b, cache, cfg)["id"] == ib["id"] and len(svc.probe.probed) == 2


def test_replaced_file_is_analyzed_again(same_names, cfg):
    a, _, cache = same_names
    svc = FakeServices()
    first = _prepare(svc, a, cache, cfg)["id"]
    a.write_bytes(b"a completely different recording")
    assert _prepare(svc, a, cache, cfg)["id"] != first and len(svc.probe.probed) == 2


def test_legacy_stem_cache_is_reused_only_for_the_same_file(same_names, cfg):
    a, b, cache = same_names
    # cache written by an earlier version: <stem>_info.json in Spanish, no size recorded
    (cache / "clip_0001_info.json").write_text(json.dumps({"ruta": str(a), "duracion": 5.0, "audio": False}))
    (cache / "clip_0001_analisis.json").write_text(json.dumps({"duracion": 5.0, "muestras": []}))
    svc = FakeServices()
    assert analyze.resolve_source_id(svc, cache, a) == "clip_0001"
    assert analyze.resolve_source_id(svc, cache, b) != "clip_0001"       # same name, other folder
    ia = _prepare(svc, a, cache, cfg)
    assert ia["id"] == "clip_0001" and svc.probe.probed == []            # nothing probed again
    assert ia["size"] == a.stat().st_size                                 # recorded from now on
    assert _prepare(svc, b, cache, cfg)["id"] != "clip_0001"
    # a recorded size that no longer matches → the file was replaced → new id
    a.write_bytes(b"replaced in place with other content")
    assert analyze.resolve_source_id(svc, cache, a) != "clip_0001"


# ── 2. a failure anywhere after the version folder exists marks it failed ───

@requires_ffmpeg
def test_bad_sfx_name_marks_the_version_failed(workspace, monkeypatch):
    from test_integration import _synthetic_project

    from social_video_editor.cli import main
    from social_video_editor.domain.models import UserError

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.chdir(workspace.parent)
    d = _synthetic_project(workspace)
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    plan["texts"][0]["sfx"] = "no_such_sound"
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(UserError, match="no_such_sound"):        # was raised outside the try
        main(["render", "demo", "--draft"])
    assert (d / "v1Borrador_fallida").is_dir() and not (d / "v1Borrador").exists()


# ── 3. ffprobe frame rate ────────────────────────────────────────────────────

def test_frame_rate_falls_back_on_zero_rates():
    assert frame_rate({"avg_frame_rate": "30000/1001"}) == pytest.approx(29.97, abs=1e-2)
    assert frame_rate({"avg_frame_rate": "0/0", "r_frame_rate": "25/1"}) == 25.0
    assert frame_rate({"avg_frame_rate": "0/0", "r_frame_rate": "0/0"}) == 30.0
    assert frame_rate({"avg_frame_rate": "N/A"}) == 30.0 and frame_rate({}) == 30.0


def test_probe_with_zero_avg_frame_rate(monkeypatch):
    out = {"format": {"duration": "2.0"},
           "streams": [{"codec_type": "video", "width": 640, "height": 360, "avg_frame_rate": "0/0",
                        "r_frame_rate": "24/1", "codec_name": "h264"}]}
    monkeypatch.setattr(probe_mod.FfprobeMediaProbe, "_run", lambda self, path: out)
    assert FfprobeMediaProbe().probe("x.mp4")["fps"] == 24.0                 # was ZeroDivisionError


# ── 4. the application layer goes through the ports ─────────────────────────

FILE_ACCESS = re.compile(r"\.(unlink|mkdir|exists|read_text|write_text|read_bytes|write_bytes|glob|iterdir|stat|"
                         r"is_dir|is_file|rename|rmdir)\(|\bopen\(|\bshutil\b|\bimportlib\b")


def test_application_does_not_touch_files_directly():
    offenders = []
    for f in sorted((SRC / "application").glob("*.py")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#")[0]
            if FILE_ACCESS.search(code) and "svc." not in code:
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


# ── 6. group duration ────────────────────────────────────────────────────────

def test_group_duration_subtracts_its_internal_transitions(cfg):
    plan = {"shots": [
        {"source": 0, "start": 0.0, "end": 2.0},
        {"source": 0, "start": 3.0, "end": 5.0, "group": "star", "transition": "fade_black"},   # 0.4 s fixed
        {"source": 0, "start": 6.0, "end": 8.0, "group": "star", "transition": "fade_black"},
        {"type": "polaroid", "from_shot": 2, "dur": 1.2, "group": "star", "transition": "cut"},
    ]}
    tl = compute_timeline(plan, cfg)
    group = next(p for p in tl.sequence.children() if isinstance(p, Group))
    shots = group.shots()
    on_screen = tl[shots[-1].index].out_end - tl[shots[0].index].out_start
    assert group.duration(30, cfg) == pytest.approx(on_screen, abs=1e-3) == pytest.approx(5.2 - 0.4, abs=1e-3)
    assert sum(s.duration(30) for s in shots) == pytest.approx(5.2)               # what view used to print
    assert Sequence.from_plan(plan).duration(30, cfg) == pytest.approx(tl.total, abs=1e-3)
