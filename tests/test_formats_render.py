"""Formats rendered for real (synthetic media, transcription written to the cache): a short of an
explainer project, subtitles in the narration box, and a voice memo with no picture."""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest
import yaml

from conftest import requires_ffmpeg
from social_video_editor.adapters.system.tools import FFMPEG
from social_video_editor.application.analyze import prepare_source
from social_video_editor.container import build_services
from test_cutaways_render import WORDS, duration, explainer_project, frame

SCRIPT = """How does a message travel?
[short travel]
And they travel apart.
Every time you send one, it splits again.
[/short]
"""


def summary_of(folder) -> dict:
    return json.loads((folder / "summary.json").read_text(encoding="utf-8"))


@pytest.mark.integration
@requires_ffmpeg
def test_a_short_is_its_own_vertical_video_and_the_long_one_ignores_it(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, SCRIPT)
    from social_video_editor.cli import main

    main(["plan", "demo"])
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    assert plan["shorts"] == [{"name": "travel", "from_sentence": 1, "to_sentence": 2}]
    main(["render", "demo", "--draft", "--short", "Travel"])
    (folder,) = [p for p in d.iterdir() if p.is_dir() and ("Short" in p.name or "Corto" in p.name)]
    video = next(folder.glob("*.mp4"))
    s = summary_of(folder)
    assert s["short"]["name"] == "travel" and s["resolution"] == "540x960"
    said = [(p["start"], p["end"]) for p in plan["sentences"][1:3]]
    assert duration(video, "v:0") == pytest.approx(sum(b - a for a, b in said) + 0.4, abs=0.6)
    main(["render", "demo", "--draft", "--shorts"])                   # never overwrites: a new folder
    assert len([p for p in d.iterdir() if p.is_dir() and ("Short" in p.name or "Corto" in p.name)]) == 2
    main(["render", "demo", "--draft"])
    long = next(p for p in d.iterdir() if p.is_dir() and p.name[1:].isdigit())
    assert "short" not in summary_of(long) and summary_of(long)["duration_sec"] > s["duration_sec"] + 1


@pytest.mark.integration
@requires_ffmpeg
def test_subtitles_in_the_narration_box(workspace, monkeypatch):
    pytest.importorskip("skia", reason="the graphics extra (skia-python) is not installed or cannot load", exc_type=ImportError)
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, "How does a message travel? And they travel apart.\n",
                          {"subtitles": {"style": "caption"}})
    from social_video_editor.cli import main

    main(["plan", "demo"])
    main(["render", "demo", "--draft"])
    folder = next(p for p in d.iterdir() if p.is_dir() and p.name.startswith("v1"))
    s = summary_of(folder)
    subs = [b for b in s["text_boxes"] if b["kind"] == "subtitle"]
    assert subs and s["auto_subtitles"] == len(subs)
    assert all(b["y"] + b["h"] <= 0.88 * 960 + 1 for b in subs)          # the explainer's check.safe_bottom
    assert list((d / "cache" / "segments").glob("subs_*.mov"))
    video = next(folder.glob("*.mp4"))
    t = (subs[0]["start"] + subs[0]["end"]) / 2
    box = frame(video, t)[subs[0]["y"] + 2:subs[0]["y"] + subs[0]["h"] - 2, subs[0]["x"] + 2:subs[0]["x"] + subs[0]["w"] - 2]
    assert box.mean() > 120                       # the paper box is drawn over the red presenter


@pytest.mark.integration
@requires_ffmpeg
def test_a_voice_memo_is_drawn_on_the_backdrop(workspace, monkeypatch):
    pytest.importorskip("skia", reason="the graphics extra (skia-python) is not installed or cannot load", exc_type=ImportError)
    monkeypatch.chdir(workspace.parent)
    memo = workspace / "memo.m4a"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=13:sample_rate=48000",
                    "-c:a", "aac", str(memo)], check=True)
    d = workspace / "projects" / "memo"
    d.mkdir(parents=True)
    (d / "project.yaml").write_text(yaml.safe_dump(
        {"name": "memo", "recipe": "explainer", "sources": [{"path": str(memo)}],
         "settings": {"framing": {"face": False}, "audio": {"clean_voice": False},
                      "script": {"support": False, "auto_broll": False}}}), encoding="utf-8")
    (d / "script.md").write_text("How does a message travel? And they travel apart.\n", encoding="utf-8")
    svc = build_services()
    project = svc.projects.load(d)
    info = prepare_source(svc, project.sources[0], project.cache, project.cfg)       # a plain proxy for the analysis
    assert not info.get("width") and duration(info["proxy"], "v:0") == pytest.approx(13, abs=0.2)
    words = [{"word": w, "start": t, "end": t + 0.25, "prob": 1.0} for w, t in WORDS]
    svc.cache.save(project.cache, "analysis", info["id"], {
        "id": info["id"], "duration": 13.0, "samples": [], "scenes": [], "words": words,
        "audio": {"step": 0.1, "db": [-20.0] * 130, "peaks": [], "median_db": -20.0},
        "voice": {"segments": [[1.0, 2.5], [4.0, 5.2], [6.0, 8.4], [10.0, 11.2]], "ratio": 0.5}})
    from social_video_editor.cli import main

    main(["plan", "memo"])
    main(["render", "memo", "--draft"])
    folder = next(p for p in d.iterdir() if p.is_dir() and p.name.startswith("v1"))
    video = next(folder.glob("*.mp4"))
    picture = frame(video, 1.0).astype(int)
    assert picture.mean() > 8 and np.ptp(picture) > 20       # the theme's backdrop, not a black frame
    assert abs(duration(video, "v:0") - duration(video, "a:0")) < 0.1
    assert any(p.name.startswith("sound_") for p in (d / "cache" / "segments").iterdir())
