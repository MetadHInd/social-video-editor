"""`sve broll` with the decider: a clip that passed only just is looked at by the local language model
(one frame of its piece, after the vision model is unloaded); a "no" turns the cue into a drawn card.
Fake sources, a fake vision model and a fake language model: nothing is downloaded or loaded."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import requires_ffmpeg
from fakes_decide import FakeDecider, FakeReasoner
from social_video_editor import cli
from social_video_editor.adapters.clip.detector import ClipVisualDetector
from social_video_editor.application import broll
from social_video_editor.domain import footage as fg
from test_cutaways_render import explainer_project
from test_footage import FakeSource, FakeVision, fake_services, stock_clip

SCRIPT = 'How does a message travel?\n[broll query="a phone on a table"]\nEvery time you send one, it splits again.\n'


class EdgeVision(FakeVision):
    """FakeVision whose clips are judged by their file name: "edge" only just over broll.min_match,
    "clear" well over it; it writes down when it is released."""

    def __init__(self, log: list):
        super().__init__()
        self.log = log

    def frame_embeddings(self, video, cache_dir, key, settings):
        self.log.append("clip")
        times = np.arange(0, 6, 0.5)
        emb = np.zeros((len(times), 3), np.float32)
        emb[:] = (0.28, 0.3, 0.1) if "edge" in Path(video).name else (0.6, 0.3, 0.1)
        return times, emb

    def release(self):
        self.log.append("clip-release")


def setup(workspace, monkeypatch, title: str, answer: str = "b", decider: dict | None = None, reason: str | None = None):
    """(project folder, the fake language model, the log of model calls) of a one-cue project."""
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, SCRIPT, {"broll": {"sources": ["stock"], "credit": False, "candidates": 1},
                                              "vision": {"enabled": True},
                                              "decider": decider or {"enabled": "auto"}})
    calls: list = []
    svc = fake_services(monkeypatch, {"stock": FakeSource("stock", "stock", [stock_clip(id="7", title=title)], "0x1EC81E")})
    svc.visual = EdgeVision(calls)
    svc.classifier = None
    svc.reasoner = FakeReasoner(answer=lambda prompt, keys: ([answer], 0.9), reason=reason, log=calls)
    cli.main(["plan", "demo"])
    return d, svc.reasoner, calls


def clip_prompts(reasoner) -> list[str]:
    return [p for p in reasoner.prompts if "frase" in p.lower() or "clara" in p.lower()]


def plan_of(d: Path) -> list[dict]:
    return json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"]


@pytest.mark.integration
@requires_ffmpeg
def test_a_clip_that_passed_only_just_and_is_turned_down_becomes_a_drawn_card(workspace, monkeypatch, capsys):
    d, reasoner, calls = setup(workspace, monkeypatch, "edge case", answer="b")       # b) no
    capsys.readouterr()
    cli.main(["broll", "demo"])
    out = capsys.readouterr().out
    (cut,) = plan_of(d)
    assert cut["type"] == "graphic" and cut["_auto"] == "card" and cut["_decided"] == "llm"
    assert "dropped" in out and "an illustrated card instead" in out and "0 fetched" in out
    assert not (d / "footage" / "stock_7_edge_case.mp4").exists()
    (asked,) = clip_prompts(reasoner)                    # the interest is clearly fine: only "does it show it?"
    assert asked.startswith("¿Esta imagen muestra lo que dice la frase? Frase: «Every time you send one")
    (image,) = [i for p, i in zip(reasoner.prompts, reasoner.images) if p == asked]
    assert image.suffix == ".jpg" and image.is_file() and image.parent.name == broll.FRAMES
    first = calls.index("llm")
    assert "clip-release" in calls[:first] and "clip" not in calls[first:]     # CLIP is unloaded before the language model


@pytest.mark.integration
@requires_ffmpeg
def test_a_clip_that_passed_only_just_and_is_approved_stays(workspace, monkeypatch, capsys):
    d, reasoner, _ = setup(workspace, monkeypatch, "edge case", answer="a")       # a) yes
    cli.main(["broll", "demo"])
    (cut,) = plan_of(d)
    assert cut["type"] == "broll" and cut["file"] == "footage/stock_7_edge_case.mp4" and cut["_decided"] == "llm"
    assert (d / cut["file"]).is_file() and len(clip_prompts(reasoner)) == 1
    (rec,) = json.loads((d / "footage" / "credits.json").read_text(encoding="utf-8"))
    assert rec["used"] is True


@pytest.mark.integration
@requires_ffmpeg
def test_a_clearly_good_clip_is_never_asked_about(workspace, monkeypatch):
    d, reasoner, _ = setup(workspace, monkeypatch, "clear one", answer="b")
    cli.main(["broll", "demo"])
    (cut,) = plan_of(d)
    assert cut["file"] == "footage/stock_7_clear_one.mp4" and "_decided" not in cut
    assert clip_prompts(reasoner) == [] and not (d / "cache" / broll.FRAMES).exists()


@pytest.mark.integration
@requires_ffmpeg
@pytest.mark.parametrize("decider, reason", [({"enabled": False}, None), ({"enabled": "auto"}, "no local server")])
def test_without_the_language_model_the_rules_keep_the_clip(workspace, monkeypatch, decider, reason):
    d, reasoner, calls = setup(workspace, monkeypatch, "edge case", answer="b", decider=decider, reason=reason)
    cli.main(["broll", "demo"])
    (cut,) = plan_of(d)
    assert cut["file"] == "footage/stock_7_edge_case.mp4" and "_decided" not in cut
    assert reasoner.prompts == [] and "clip-release" not in calls and not (d / "cache" / broll.FRAMES).exists()


# ── the rules ────────────────────────────────────────────────────────────────
def test_borderline_and_how_sure_the_rules_are():
    s = fg.settings(None)                                   # min_match 0.27 · min_interest 0.05
    assert broll.borderline(None, None, s) is None          # not judged (no vision model): nothing to doubt
    assert broll.borderline(0.40, 0.20, s) is None
    match, alive = broll.borderline(0.28, 0.20, s)
    assert match == pytest.approx(0.01) and alive == pytest.approx(0.15)
    assert broll.borderline(0.40, 0.06, s)[1] == pytest.approx(0.01)
    assert broll.sureness(0.0) == 0.5 and broll.sureness(0.015) == 0.75 and broll.sureness(0.2) == 0.99
    assert broll.sureness(-0.01) == 0.5


def test_the_questions_and_a_decider_that_says_no():
    d = {"said": "Every time you send one", "match": 0.01, "interest": 0.15}
    shows, lively = broll.clip_questions(d, "frame.jpg", "en")
    assert (shows.topic, lively.topic) == ("clip_shows", "clip_lively")
    assert shows.prompt == "Does this picture show what the sentence says? Sentence: “Every time you send one”"
    assert shows.default == lively.default == ("yes",) and shows.image == lively.image == "frame.jpg"
    assert shows.confidence < 0.8 <= lively.confidence      # only the doubtful one goes to the models
    es = broll.clip_questions(d, "frame.jpg", "es")[1]
    assert es.prompt.startswith("¿La imagen muestra algo pasando") and es.options[0].text == "sí"

    plan = {"cutaways": [{"type": "broll", "query": "q", "file": "footage/x.mp4"}]}
    cues = [{"n": 0, "status": "fetched"}]
    doubt = {"n": 0, "before": {"type": "broll", "query": "q"}, "file": "footage/x.mp4", "at": 1.0, **d}
    frames = {0: Path("frame.jpg")}                          # already taken: no FFmpeg here
    no = FakeDecider({"clip_shows": "no"})
    drop, by, cards = broll._settle(None, None, plan, cues, [doubt], frames, {}, "phone", True, "en", no)
    assert drop == {0: "llm"} and by == {0: "llm"} and cards == {0: {}}
    assert no.topics() == ["clip_shows", "clip_lively"]
    rules = broll._settle(None, None, plan, cues, [doubt], {}, {}, "phone", True, "en", broll.dd.rules_only)
    assert rules == ({}, {}, {})                             # the rules keep the clip and look at nothing


def test_the_vision_model_frees_its_memory(monkeypatch):
    freed = []
    fake_torch = SimpleNamespace(mps=SimpleNamespace(empty_cache=lambda: freed.append("mps")),
                                 cuda=SimpleNamespace(is_available=lambda: False, empty_cache=lambda: freed.append("cuda")))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    det = ClipVisualDetector()
    det._models[("m", "cpu")] = object()
    det.release()
    assert det._models == {} and freed == ["mps"]
    monkeypatch.delitem(sys.modules, "torch")
    det.release()                                            # without torch loaded: nothing else to free
