"""transcription.engine: choosing faster-whisper or the Apple GPU engine, falling back, the worker's
helpers (word times back on the original timeline, DTW) and `sve doctor`'s line. No model is loaded."""
from __future__ import annotations

import json
import sys

import numpy as np
import pytest

from social_video_editor.adapters.mlx_whisper import transcriber as mlx
from social_video_editor.adapters.mlx_whisper.worker import dtw, words_of
from social_video_editor.adapters.system.doctor import transcription_checks
from social_video_editor.adapters.whisper import transcriber as tr
from social_video_editor.adapters.whisper.transcriber import EngineTranscriber, engine_setting, run_worker

WORDS = [{"word": "hello", "start": 0.1, "end": 0.4, "prob": 0.9}]


class FakeEngine:
    def __init__(self, name, why=None, words=WORDS, device="test device"):
        self.name, self.why, self.words, self.device, self.calls = name, why, words, device, []

    def ready(self, settings=None):
        return self.why

    def run(self, wav, settings, workdir):
        self.calls.append(settings.get("engine"))
        return self.words


def engines(mlx_why=None, mlx_words=WORDS, cpu_words=WORDS):
    cpu = FakeEngine("faster-whisper", words=cpu_words)
    gpu = FakeEngine("mlx", why=mlx_why, words=mlx_words)
    return EngineTranscriber(cpu, [gpu]), cpu, gpu


@pytest.mark.parametrize("raw, expected", [
    (None, "faster-whisper"), ("", "faster-whisper"), ("auto", "auto"), ("AUTO", "auto"), ("mlx", "mlx"),
    ("mlx-whisper", "mlx"), ("apple", "mlx"), ("faster_whisper", "faster-whisper"), ("cpu", "faster-whisper"),
    ("whisperx", "faster-whisper"),                       # unknown: warned, the default
])
def test_engine_setting(raw, expected):
    assert engine_setting({"engine": raw}) == expected


def test_default_and_missing_setting_use_faster_whisper():
    t, cpu, gpu = engines()
    assert t.choose({}) is cpu and t.choose({"engine": "faster-whisper"}) is cpu
    words, info = t.transcribe_info("a.wav", {"model": "small"}, None)
    assert words == WORDS and info == {"engine": "faster-whisper", "model": "small", "device": "test device"}
    assert gpu.calls == []


def test_auto_and_explicit_pick_the_accelerated_engine_when_it_is_ready():
    t, cpu, gpu = engines()
    assert t.choose({"engine": "auto"}) is gpu and t.choose({"engine": "mlx"}) is gpu
    _, info = t.transcribe_info("a.wav", {"engine": "auto"}, None)
    assert info["engine"] == "mlx" and cpu.calls == []


def test_an_engine_that_cannot_run_here_means_faster_whisper(capsys):
    t, cpu, gpu = engines(mlx_why="needs a Mac with Apple Silicon")
    assert t.choose({"engine": "auto"}) is cpu
    assert capsys.readouterr().out == ""                  # auto: silently the default
    assert t.choose({"engine": "mlx"}) is cpu
    assert "needs a Mac with Apple Silicon" in capsys.readouterr().out


def test_a_failing_engine_falls_back_to_faster_whisper(capsys):
    t, cpu, gpu = engines(mlx_words=None)
    words, info = t.transcribe_info("a.wav", {"engine": "mlx", "model": "base"}, None)
    assert words == WORDS and info["engine"] == "faster-whisper" and info["model"] == "base"
    assert gpu.calls == ["mlx"] and cpu.calls == ["mlx"]
    assert "transcription with mlx failed; transcribing with faster-whisper" in capsys.readouterr().out


def test_when_every_engine_fails_there_are_no_words():
    t, _, _ = engines(mlx_words=None, cpu_words=None)
    words, info = t.transcribe_info("a.wav", {"engine": "auto"}, None)
    assert words == [] and info["engine"] == "faster-whisper"
    assert t.transcribe("a.wav", {"engine": "auto"}, None) == []


def test_run_worker_reads_the_words_and_names_files_after_the_wav(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "fake_sve_worker.py").write_text(
        "import json, sys\n"
        "cfg = json.load(open(sys.argv[2]))\n"
        "json.dump([{'word': cfg['language'], 'start': 0.0, 'end': 0.5, 'prob': 1.0}], open(sys.argv[3], 'w'))\n",
        encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(mods))
    work = tmp_path / "cache"
    work.mkdir()
    words = run_worker("fake_sve_worker", str(tmp_path / "clip_a_16k.wav"), {"language": "es"}, work)
    assert words == [{"word": "es", "start": 0.0, "end": 0.5, "prob": 1.0}]
    assert (work / "_whisper_clip_a_16k.json").is_file() and (work / "clip_a_16k_words.json").is_file()
    # two sources never share a config file (they may be transcribed side by side)
    run_worker("fake_sve_worker", str(tmp_path / "clip_b_16k.wav"), {"language": "en"}, work)
    assert json.loads((work / "_whisper_clip_a_16k.json").read_text())["language"] == "es"


def test_run_worker_failure_is_none(tmp_path, capsys):
    assert run_worker("no_such_module_for_sve_tests", str(tmp_path / "x.wav"), {}, tmp_path, "mlx") is None
    assert "transcription (mlx) failed" in capsys.readouterr().out


def test_mlx_model_names_follow_faster_whisper_sizes():
    assert mlx.model_repo("small") == "mlx-community/whisper-small-mlx"
    assert mlx.model_repo(None) == "mlx-community/whisper-small-mlx"
    assert mlx.model_repo("large-v3-turbo") == "mlx-community/whisper-large-v3-turbo"
    assert mlx.model_repo("/models/my-whisper") == "/models/my-whisper"      # a folder or repo passes through


def test_mlx_ready(monkeypatch, tmp_path):
    t = mlx.MlxWhisperTranscriber()
    monkeypatch.setattr(mlx, "apple_silicon", lambda: False)
    assert "Apple Silicon" in t.ready({})
    monkeypatch.setattr(mlx, "apple_silicon", lambda: True)
    monkeypatch.setattr(mlx.importlib.util, "find_spec", lambda name: None)
    assert "not installed" in t.ready({})
    monkeypatch.setattr(mlx.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("SVE_OFFLINE", "1")
    assert "not downloaded" in t.ready({"model": "small"})
    snap = tmp_path / "models--mlx-community--whisper-small-mlx" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "weights.npz").write_bytes(b"x")
    assert t.ready({"model": "small"}) is None
    monkeypatch.delenv("SVE_OFFLINE")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)      # the CI runs with downloads off
    assert t.ready({"model": "medium"}) is None          # online: downloaded on first use


def test_mlx_ready_never_imports_mlx(monkeypatch):
    monkeypatch.delitem(sys.modules, "mlx_whisper", raising=False)
    mlx.MlxWhisperTranscriber().ready({})
    assert "mlx_whisper" not in sys.modules


class Map:
    """Two speech chunks: 0–2 s kept, 2–5 s silence cut out, then 5–8 s (speech time 2 s = 5 s)."""
    def get_chunk_index(self, t):
        return 0 if t < 2.0 else 1

    def get_original_time(self, t, k):
        return round(t + (3.0 if k == 1 else 0.0), 2)


def test_words_of_restores_original_times_and_filters_like_faster_whisper():
    result = {"segments": [{"words": [
        {"word": " Hola", "start": 0.5, "end": 0.9, "probability": 0.91},
        {"word": " eh", "start": 1.0, "end": 1.1, "probability": 0.2},           # below min_probability
        {"word": "  ", "start": 1.2, "end": 1.3, "probability": 0.99},           # blank
        {"word": " mundo.", "start": 1.98, "end": 2.4, "probability": 0.8},      # middle in the 2nd chunk
    ]}, {"words": []}]}
    assert words_of(result, Map(), 0.35) == [
        {"word": "Hola", "start": 0.5, "end": 0.9, "prob": 0.91},
        {"word": "mundo.", "start": 4.98, "end": 5.4, "prob": 0.8}]
    assert words_of({"segments": [{"words": [{"word": "x", "start": 1, "end": 2, "probability": 1}]}]}, None, 0.35) \
        == [{"word": "x", "start": 1.0, "end": 2.0, "prob": 1.0}]


def reference_dtw(x):
    """The textbook loop (mlx-whisper's / openai-whisper's numba version, without numba)."""
    N, M = x.shape
    cost = np.full((N + 1, M + 1), np.inf, dtype=np.float32)
    trace = -np.ones((N + 1, M + 1), dtype=np.float32)
    cost[0, 0] = 0
    for j in range(1, M + 1):
        for i in range(1, N + 1):
            c0, c1, c2 = cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1]
            if c0 < c1 and c0 < c2:
                c, t = c0, 0
            elif c1 < c0 and c1 < c2:
                c, t = c1, 1
            else:
                c, t = c2, 2
            cost[i, j] = x[i - 1, j - 1] + c
            trace[i, j] = t
    i, j = trace.shape[0] - 1, trace.shape[1] - 1
    trace[0, :], trace[:, 0] = 2, 1
    path = []
    while i > 0 or j > 0:
        path.append((i - 1, j - 1))
        t = trace[i, j]
        i, j = (i - 1, j - 1) if t == 0 else (i - 1, j) if t == 1 else (i, j - 1)
    return np.array(path[::-1]).T


def test_dtw_matches_the_reference_including_ties():
    rng = np.random.default_rng(7)
    for k in range(60):
        n, m = int(rng.integers(1, 12)), int(rng.integers(1, 30))
        x = (rng.integers(0, 3, (n, m)) if k % 2 else rng.normal(size=(n, m))).astype(np.float32)
        assert np.array_equal(dtw(x), reference_dtw(x)), (k, n, m)


def test_doctor_names_the_engine_settings_pick():
    t, _, _ = engines(mlx_why="not installed (pip install x)")
    (status, name, detail), = transcription_checks(t, {"transcription": {"engine": "faster-whisper"}})
    assert status == "ok" and detail.startswith("faster-whisper on test device")
    (_, _, detail), = transcription_checks(t, {"transcription": {"engine": "mlx"}})
    assert detail.startswith("faster-whisper") and "mlx: not installed" in detail
    t, _, _ = engines()
    (_, _, detail), = transcription_checks(t, {"transcription": {"engine": "auto"}})
    assert detail.startswith("mlx on")
    assert transcription_checks(object(), {}) == []


def test_container_wires_the_engine_choice():
    from social_video_editor.container import build_services

    t = build_services().transcriber
    assert isinstance(t, EngineTranscriber) and isinstance(t.default, tr.WhisperTranscriber)
    assert [e.name for e in t.engines()] == ["faster-whisper", "mlx"]
