"""`sve check`: pure verdicts (domain.check) and a run on a tiny synthetic render."""
from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
import yaml

from conftest import requires_ffmpeg
from social_video_editor.domain import check as ck

SR = 48000


# ── pure logic ───────────────────────────────────────────────────────────────

def test_silences_and_clipping():
    x = np.concatenate([np.full(SR, 0.1), np.zeros(SR), np.full(SR // 10, 0.1)]).astype("float32")
    assert ck.silences(x, SR, -50, 0.5) == [(1.0, 2.0)]
    assert ck.silences(x, SR, -50, 1.5) == []
    y = np.zeros(1000, "float32")
    y[100:103] = 1.0
    y[500] = -1.0                                       # a single sample is not a clipped run
    assert ck.clipping(y, 1000) == [(0.1, 3)]


def test_harshness_band_ratio():
    t = np.arange(SR) / SR
    low, mid = np.sin(2 * np.pi * 300 * t), np.sin(2 * np.pi * 3000 * t)
    assert ck.band_ratio(low, SR) < 0.01
    assert ck.band_ratio(mid, SR) > 0.95
    h = ck.harshness(np.concatenate([low, mid, low]).astype("float32"), SR)
    assert 0.2 < h["ratio"] < 0.5 and 1.0 <= h["worst_t"] <= 2.0 and h["worst_ratio"] > 0.9


def test_cut_jumps_typing_and_beats():
    t = [round(0.1 * k, 1) for k in range(1, 51)]
    m = [-20.0 if x <= 2.0 else -35.0 for x in t]
    jumps = ck.cut_jumps(t, m, [2.0, 4.0])
    assert jumps[0]["jump_lu"] == -15.0 and jumps[1]["jump_lu"] == 0.0
    assert ck.cut_jumps(t, [-80.0] * 50, [2.0]) == []                 # silence on both sides: ignored
    bursts = ck.typing_bursts([0.0, 0.03, 0.06, 0.09, 1.0, 1.1])
    assert bursts == [[0.0, 0.09, 4], [1.0, 1.1, 2]]
    assert ck.burst_rate(bursts[0]) == pytest.approx(33.3, abs=0.1) and ck.burst_rate(bursts[1]) == 0
    assert ck.beat_deviation([1.02, 1.26], [0.5, 1.0, 1.5]) == [0.02, 0.24]
    assert ck.beat_deviation([1.26], [0.5, 1.0, 1.5], half_beats=True) == [0.01]


def test_box_issues_safe_zone():
    th = ck.thresholds()
    W, H = 1080, 1920
    boxes = [{"text": "ok", "x": 100, "y": 600, "w": 800, "h": 120, "start": 0, "end": 1},
             {"text": "high", "x": 100, "y": 50, "w": 800, "h": 120, "start": 0, "end": 1},
             {"text": "low", "x": 100, "y": 1450, "w": 800, "h": 120, "start": 0, "end": 1},
             {"text": "buttons", "x": 500, "y": 1000, "w": 560, "h": 100, "start": 0, "end": 1},
             {"text": "corner", "x": 850, "y": 130, "w": 200, "h": 60, "start": 0, "end": 1}]   # above the buttons
    issues = {i["text"]: i["issues"] for i in ck.box_issues(boxes, W, H, th)}
    assert set(issues) == {"high", "low", "buttons"}
    assert "top" in issues["high"][0] and "bottom" in issues["low"][0] and "right" in issues["buttons"][0]


def test_pick_moments_and_windows():
    cands = [{"kind": "loud_jump", "t": 1.0, "severity": 3}, {"kind": "loud_jump", "t": 1.5, "severity": 2},
             {"kind": "loud_jump", "t": 8.0, "severity": 1.5}, {"kind": "loud_jump", "t": 12.0, "severity": 1.4},
             {"kind": "silence", "t": 5.0, "severity": 1.2}]
    picks = ck.pick_moments(cands, 6)
    assert [(p["kind"], p["t"]) for p in picks] == [("loud_jump", 1.0), ("loud_jump", 8.0), ("silence", 5.0)]
    assert ck.snippet_window(0.5, 3.0, 10.0) == (0.0, 3.0)
    assert ck.snippet_window(9.8, 3.0, 10.0) == (7.0, 3.0)
    assert ck.snippet_window(1.0, 3.0, 2.0) == (0.0, 2.0)


def test_thresholds_layering():
    th = ck.thresholds({"cut_jump_lu": 5, "silence_sec": None}, {"cut_jump_lu": 4})
    assert th["cut_jump_lu"] == 4 and th["silence_sec"] == ck.DEFAULTS["silence_sec"]


def test_base_recipe_check_section_matches_defaults(cfg):
    assert cfg["check"] == ck.DEFAULTS


# ── on a real (tiny, synthetic) render ───────────────────────────────────────

def _snapshot(folder):
    return {p.relative_to(folder): p.stat().st_mtime_ns for p in folder.rglob("*")}


@pytest.mark.integration
@requires_ffmpeg
def test_check_on_synthetic_render(workspace, monkeypatch, tmp_path, capsys):
    from test_integration import _synthetic_project

    from social_video_editor.adapters.files.jsonio import read_json, write_json
    from social_video_editor.adapters.system.tools import FFPROBE
    from social_video_editor.cli import main

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.chdir(workspace.parent)
    d = _synthetic_project(workspace)
    main(["render", "demo", "--draft"])
    vdir = d / "v1Borrador"

    # the render wrote stems in the same pass + text boxes and typing bursts in the summary
    for k in ("original", "music", "sfx"):
        assert (vdir / "stems" / f"{k}.wav").exists(), k
    summary = read_json(vdir / "summary.json")
    assert summary["stems"] == {k: f"stems/{k}.wav" for k in ("original", "music", "sfx")}
    assert {b["text"] for b in summary["text_boxes"]} == {"Hola 👀", "fin"}
    for b in summary["text_boxes"]:
        assert 0 <= b["x"] and b["x"] + b["w"] <= 540 and 0 <= b["y"] and b["y"] + b["h"] <= 960
        assert b["end"] > b["start"] and b["w"] > 0 and b["h"] > 0
    assert summary["typing"] and summary["audio"]["master_gain_db"] is not None
    import subprocess
    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(vdir / "stems" / "sfx.wav")], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(summary["duration_sec"], abs=0.05)

    # strict thresholds in the project so that there are moments to listen to
    data = yaml.safe_load((d / "project.yaml").read_text(encoding="utf-8"))
    data["settings"]["check"] = {"cut_jump_lu": 0.01, "typing_max_per_sec": 1}
    (d / "project.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    main(["check", "demo", "--version", "v1Borrador"])
    out = capsys.readouterr().out
    assert "Check v1Borrador" in out
    result = json.loads((vdir / "check.json").read_text(encoding="utf-8"))
    assert list(result) == ["version", "ok", "errors", "warnings", "metrics", "listen"]
    assert result["version"] == "v1Borrador" and isinstance(result["ok"], bool)
    m = result["metrics"]
    # BS.1770 (ebur128) on a 5 s, very dynamic clip differs from the mastering's loudnorm reading
    assert m["audio"]["integrated_lufs"] == pytest.approx(-14, abs=2.0)
    assert m["audio"]["engine_lufs"] == pytest.approx(-14, abs=1.0)
    assert m["audio"]["clipped_runs"] == 0 and m["audio"]["true_peak_dbtp"] <= -1.0
    assert set(m["stems"]) >= {"original", "music", "sfx", "master_gain_db"}
    assert m["beats"]["cuts"] == 2 and m["beats"]["median_ms"] <= 50          # cuts on the 120 BPM clicks
    assert m["typing"]["max_per_sec"] > 1 and m["text_boxes"]["count"] == 2
    assert not any("true peak" in e or "clipping" in e for e in result["errors"])
    assert any("typing too dense" in w for w in result["warnings"])
    assert result["listen"] and all(p.startswith("escuchar/") for p in result["listen"])   # language: es
    for p in result["listen"]:
        assert (vdir / p).exists()
        assert float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                     str(vdir / p)], capture_output=True, text=True).stdout) <= 3.05

    # --out: nothing in the version folder changes
    before = _snapshot(vdir)
    main(["verificar", "demo", "--version", "v1Borrador", "--out", str(tmp_path / "out")])
    assert _snapshot(vdir) == before
    assert (tmp_path / "out" / "check.json").exists()
    assert json.loads((tmp_path / "out" / "check.json").read_text())["listen"] == result["listen"]

    # an old render (no stems, no text boxes / typing in the summary) is still checked
    old = d / "v2"
    shutil.copytree(vdir, old)
    shutil.rmtree(old / "stems")
    (old / "check.json").unlink()
    shutil.rmtree(old / "escuchar")
    s = read_json(old / "summary.json")
    for k in ("stems", "text_boxes", "typing"):
        s.pop(k)
    s["audio"].pop("master_gain_db")
    write_json(old / "summary.json", s)
    main(["check", "demo", "--out", str(tmp_path / "old")])           # default: the latest main version (v2)
    r = json.loads((tmp_path / "old" / "check.json").read_text())
    assert r["version"] == "v2" and "stems" not in r["metrics"]
    notes = " ".join(r["metrics"]["notes"])
    assert "no stems" in notes and "estimated" in notes and "typing bursts not recorded" in notes
    assert r["metrics"]["text_boxes"]["count"] == 2
    assert not (old / "check.json").exists()
