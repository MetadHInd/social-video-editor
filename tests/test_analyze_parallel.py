"""Sources analyzed side by side (analysis.jobs): same results and cache files as one at a time, one
Whisper at a time, a file listed twice worked on once, the engine recorded and kept on re-analysis.
Every service is a fake (no FFmpeg, no model)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from social_video_editor.adapters.files.cache import JsonCacheStore
from social_video_editor.application import analyze
from social_video_editor.domain.models import UserError


class Probe:
    def fingerprint(self, path):
        p = Path(path)
        return {"path": str(p), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}

    def probe(self, path):
        n = int(Path(path).read_text())
        return {"path": str(path), "duration": 10.0 + n, "width": 1080, "height": 1920, "has_audio": n % 4 != 3,
                "audio_index": 1}


class Encoder:
    def __init__(self, jobs=3):
        self.jobs = jobs

    def parallel_jobs(self, cfg):
        return self.jobs

    def make_proxy(self, info, out, hdr_white):
        time.sleep(0.02)
        Path(out).write_text(f"proxy of {Path(info['path']).name}")

    def extract_wav(self, info, out):
        Path(out).write_text(f"wav of {Path(info['path']).name}")


class Video:
    def samples(self, proxy, per_second):
        time.sleep(0.01)
        return [{"t": 0.0, "brightness": len(Path(proxy).read_text()) / 100}]

    def scenes(self, proxy, threshold):
        return [[0.0, 1.0]]

    def face(self, proxy, download=False):
        return None


class Audio:
    def levels(self, wav):
        return {"step": 0.1, "db": [-20.0], "peaks": [], "median_db": -20.0}


class Transcriber:
    """Words from the wav's text; counts calls and the most transcriptions running at once."""
    def __init__(self, with_info=True):
        self.calls, self.settings, self.running, self.most = [], [], 0, 0
        self.lock = threading.Lock()
        if with_info:
            self.transcribe_info = self._info

    def transcribe(self, wav, settings, workdir):
        with self.lock:
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(0.03)
        with self.lock:
            self.running -= 1
            self.calls.append(Path(wav).name)
            self.settings.append(dict(settings))
        text = Path(wav).read_text()
        return [{"word": w, "start": k * 0.5, "end": k * 0.5 + 0.4, "prob": 1.0} for k, w in enumerate(text.split())]

    def _info(self, wav, settings, workdir):
        return self.transcribe(wav, settings, workdir), {"engine": settings.get("engine") or "faster-whisper",
                                                         "model": "small", "device": "fake"}


def services(jobs=3, transcriber=None):
    return SimpleNamespace(probe=Probe(), encoder=Encoder(jobs), video=Video(), audio=Audio(),
                           transcriber=transcriber or Transcriber(), cache=JsonCacheStore(), photos=None)


def make_project(tmp_path, name, paths, settings=None):
    d = tmp_path / name
    (d / "cache").mkdir(parents=True)
    cfg = {"color": {}, "analysis": {}, "transcription": {"model": "small"}, "framing": {"face": False}}
    for k, v in (settings or {}).items():
        cfg[k] = {**cfg.get(k, {}), **v}
    return SimpleNamespace(sources=[{"path": str(p)} for p in paths], cache=d / "cache", cfg=cfg)


@pytest.fixture
def clips(tmp_path):
    out = []
    for n in range(6):
        p = tmp_path / "media" / f"clip_{n}.mov"
        p.parent.mkdir(exist_ok=True)
        p.write_text(str(n))
        out.append(p)
    return out


def contents(cache: Path) -> dict:
    """Cache files by name (paths inside them made relative to the cache folder)."""
    escaped = json.dumps(str(cache))[1:-1]                    # inside JSON files (backslashes doubled on Windows)
    return {f.name: f.read_text(encoding="utf-8").replace(escaped, "<cache>").replace(str(cache), "<cache>")
            for f in sorted(cache.iterdir())}


def test_parallel_gives_the_same_results_and_cache_files(tmp_path, clips):
    serial = make_project(tmp_path, "serial", clips, {"analysis": {"jobs": 1}})
    infos_1, an_1 = analyze.prepare_sources(services(), serial)
    parallel = make_project(tmp_path, "parallel", clips)
    svc = services(jobs=4)
    infos_n, an_n = analyze.prepare_sources(svc, parallel)
    inside = lambda path: json.dumps(str(path))[1:-1]            # as it is written in JSON (backslashes doubled on Windows)
    strip = lambda x: json.loads(json.dumps(x).replace(inside(serial.cache), "<cache>").replace(inside(parallel.cache), "<cache>"))
    assert strip(infos_1) == strip(infos_n) and strip(an_1) == strip(an_n)
    assert contents(serial.cache) == contents(parallel.cache)
    assert [a["id"] for a in an_n] == [i["id"] for i in infos_n]               # story order
    # one Whisper at a time, in story order, only the sources with sound
    assert svc.transcriber.most == 1
    assert svc.transcriber.calls == [f"{i['id']}_16k.wav" for i in infos_n if i["has_audio"]]
    assert all(a["transcription"] == {"engine": "faster-whisper", "model": "small", "device": "fake"}
               for i, a in zip(infos_n, an_n) if i["has_audio"])
    assert all("transcription" not in a and a["words"] == [] for i, a in zip(infos_n, an_n) if not i["has_audio"])


def test_a_file_listed_twice_is_worked_on_once(tmp_path, clips):
    project = make_project(tmp_path, "twice", [clips[0], clips[1], clips[0]])
    svc = services()
    infos, analyses = analyze.prepare_sources(svc, project)
    assert len(infos) == 3 and infos[0] == infos[2] and infos[0] is not infos[2]
    assert analyses[0] == analyses[2] and analyses[0] is not analyses[2]
    assert len(svc.transcriber.calls) == 2


def test_cached_analyses_are_not_transcribed_again_and_force_keeps_the_engine(tmp_path, clips):
    project = make_project(tmp_path, "again", clips[:3], {"transcription": {"engine": "auto"}})
    svc = services()
    analyze.prepare_sources(svc, project)
    assert len(svc.transcriber.calls) == 3
    # an analysis written before engines were recorded = faster-whisper; another one by the GPU engine
    ids = sorted(f.name.removesuffix("_analysis.json") for f in project.cache.glob("*_analysis.json"))
    first = json.loads((project.cache / f"{ids[0]}_analysis.json").read_text())
    first.pop("transcription")
    (project.cache / f"{ids[0]}_analysis.json").write_text(json.dumps(first))
    second = json.loads((project.cache / f"{ids[1]}_analysis.json").read_text())
    second["transcription"]["engine"] = "mlx"
    (project.cache / f"{ids[1]}_analysis.json").write_text(json.dumps(second))
    svc = services()
    analyze.prepare_sources(svc, project)
    assert svc.transcriber.calls == []                                       # all cached
    svc = services()
    analyze.prepare_sources(svc, project, force=True)
    engines = {c: s["engine"] for c, s in zip(svc.transcriber.calls, svc.transcriber.settings)}
    assert engines[f"{ids[0]}_16k.wav"] == "faster-whisper" and engines[f"{ids[1]}_16k.wav"] == "mlx"


def test_an_explicit_engine_wins_over_the_previous_one():
    previous = {"transcription": {"engine": "mlx"}}
    assert analyze.transcription_settings({"transcription": {"engine": "faster-whisper"}}, previous)["engine"] \
        == "faster-whisper"
    assert analyze.transcription_settings({"transcription": {"engine": "auto"}}, None)["engine"] == "auto"
    assert analyze.transcription_settings({"transcription": {"engine": "auto"}}, {})["engine"] == "faster-whisper"
    assert analyze.transcription_settings({}, previous) == {}


def test_a_transcriber_without_engine_info_still_works(tmp_path, clips):
    project = make_project(tmp_path, "plain", clips[:2])
    _, analyses = analyze.prepare_sources(services(transcriber=Transcriber(with_info=False)), project)
    assert all(a["words"] and "transcription" not in a for a in analyses)


def test_analysis_jobs():
    svc = services(jobs=3)
    assert analyze.analysis_jobs(svc, {}) == 3
    assert analyze.analysis_jobs(svc, {"analysis": {"jobs": "auto"}}) == 3
    assert analyze.analysis_jobs(svc, {"analysis": {"jobs": 1}}) == 1
    assert analyze.analysis_jobs(svc, {"analysis": {"jobs": "2"}}) == 2
    assert analyze.analysis_jobs(SimpleNamespace(encoder=object()), {}) == 1
    with pytest.raises(UserError):
        analyze.analysis_jobs(svc, {"analysis": {"jobs": "many"}})


def test_an_error_in_one_source_is_raised(tmp_path, clips):
    project = make_project(tmp_path, "broken", clips[:4])
    svc = services()

    def broken(proxy, threshold):
        raise RuntimeError("scene detector exploded")

    svc.video.scenes = broken
    with pytest.raises(RuntimeError, match="exploded"):
        analyze.prepare_sources(svc, project)


def test_in_parallel_keeps_order():
    assert analyze.in_parallel(lambda x: x * 2, [3, 1, 2], 3) == [6, 2, 4]
    assert analyze.in_parallel(lambda x: {"v": x}, [1, 1], 2, key=lambda x: x) == [{"v": 1}, {"v": 1}]
