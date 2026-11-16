"""scripts/bench.py's speech scenarios (analyze, explainer75): the pure parts. Nothing is generated."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from social_video_editor.domain.script import parse_script

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture(scope="module")
def bench():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("sve_bench_speech", SCRIPTS / "bench.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.path.remove(str(SCRIPTS))


def test_speech_scenarios_run_only_when_named(bench):
    assert bench.scenario_keys(bench.parse_args([])) == list(bench.SCENARIOS)
    a = bench.parse_args(["--scenario", "analyze", "--quick", "--engine", "mlx", "--analysis-jobs", "1"])
    assert bench.scenario_keys(a) == ["analyze-quick"] and (a.engine, a.analysis_jobs) == ("mlx", "1")
    assert bench.scenario_keys(bench.parse_args(["--scenario", "explainer75"])) == ["explainer75"]
    for name in bench.EXTRA_SCENARIOS:
        assert name in bench.PARAMS and f"{name}-quick" in bench.PARAMS


def test_explainer_script_has_six_graphics_and_every_sentence(bench):
    p = bench.PARAMS["explainer75"]
    text, sentences = bench.explainer_script(p["sentences"], p["graphics"])
    script = parse_script(text)
    assert [s.text for s in script.sentences] == sentences and len(sentences) == 18
    cues = [line for line in text.splitlines() if line.startswith("[")]
    graphics = [c for c in cues if c != "[camera]"]
    assert len(graphics) == 6 and len({c.split()[0] for c in graphics}) == 6
    assert cues.count("[camera]") == 6                           # back to the presenter after each one


def test_timed_words_fill_the_recording(bench):
    _, sentences = bench.explainer_script(18, 6)
    words = bench.timed_words(sentences, 75.0)
    assert len(words) == sum(len(s.split()) for s in sentences)
    assert words[0]["start"] == 0.5 and 74.0 < words[-1]["end"] < 75.0
    assert all(a["end"] <= b["start"] for a, b in zip(words, words[1:]))


def test_narration_is_about_the_asked_length(bench):
    text = bench.narration(3, 30.0)
    assert 30 * 2.4 <= len(text.split()) < 30 * 2.4 + 20 and text.startswith(bench.SENTENCES[3])


def test_transcription_seconds_from_the_log(bench):
    line = lambda t, msg: (t, f"[sve 12:00:00]   {msg}")
    lines = [line(1.0, "transcription (a.mov)…"), line(2.0, "audio (b.mov)…"),
             line(4.5, "words (a.mov): 10 (faster-whisper, small, CPU (int8))"),
             line(4.5, "transcription (b.mov)…"), line(6.0, "words (b.mov): 3"), line(7.0, "done (b.mov): …")]
    assert bench.transcription_seconds(lines) == 5.0
    assert bench.transcription_seconds([line(1.0, "audio (a.mov)…")]) is None


def test_analysis_summary_reads_the_engines(bench, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "a_analysis.json").write_text(json.dumps(
        {"words": [1, 2], "transcription": {"engine": "faster-whisper", "model": "small", "device": "CPU (int8)"}}))
    (cache / "b_analysis.json").write_text(json.dumps({"words": [1]}))
    s = bench.analysis_summary(tmp_path)
    assert s["words"] == 3
    assert {"engine": "faster-whisper", "model": "small", "device": "CPU (int8)"} in s["transcription"]
    assert any(e["engine"].endswith("(not recorded)") for e in s["transcription"])


def test_clean_project_whole_cache_and_extra_folders(bench, tmp_path):
    (tmp_path / bench.MARKER).write_text("x", encoding="utf-8")
    project = tmp_path / "projects" / "p"
    for sub in ("segments", "voice", "keep"):
        (project / "cache" / sub).mkdir(parents=True)
    bench.clean_project(tmp_path, project, extra=("voice",))
    assert sorted(p.name for p in (project / "cache").iterdir()) == ["keep"]
    bench.clean_project(tmp_path, project, cache=True)
    assert not (project / "cache").exists()
