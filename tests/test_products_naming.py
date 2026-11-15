"""How products get their names (vision.naming): clip / frames / decider. Synthetic embeddings, fake models."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from fakes_decide import FakeDecider, FakeReasoner, FakeVisual
from social_video_editor.adapters.clip.detector import ClipVisualDetector
from social_video_editor.adapters.files import legacy_es
from social_video_editor.adapters.files.cache import JsonCacheStore
from social_video_editor.adapters.files.recipes import YamlRecipeRepository
from social_video_editor.application import products as app
from social_video_editor.domain import decide as dd
from social_video_editor.domain import products as dp
from social_video_editor.domain.models import Project
from test_products import FakeDetector, footage

LABELS = ["taza", "aretes", "botella", "gorra"]


def _detect(naming: str, naming_vocabulary=None, **extra):
    t, emb, vocab, an = footage()
    src = [{"t": t, "emb": emb, "analysis": an}]
    z = np.zeros((0, emb.shape[1]), np.float32)
    v = {"min_score": 0.5, "naming": naming, **extra}
    return dp.detect_products(src, z, z, vocab, LABELS, v, None, naming_vocabulary)


# ── domain ───────────────────────────────────────────────────────────────────

def test_naming_defaults_to_clip_and_unknown_values_fall_back():
    assert dp.settings(None)["naming"] == "clip"
    assert dp.naming_mode(dp.settings({"naming": "Decider"})) == "decider"
    assert dp.naming_mode(dp.settings({"naming": "magic"})) == "clip"


def test_clip_naming_is_unchanged_and_has_no_working_keys():
    before = _detect("clip")
    assert [s["label"] for s in before] == ["aretes", "botella"]
    assert all(not any(k.startswith("_") for k in s) for s in before)
    t, emb, vocab, _ = footage()
    assert _detect("clip", naming_vocabulary=np.flip(vocab, 0).copy()) == before      # ignored in clip mode


def test_frames_naming_uses_the_best_frames_and_lists_candidates():
    found = _detect("frames", name_frames=2)
    assert [s["label"] for s in found] == ["aretes", "botella"]
    for s in found:
        assert s["_candidates"][0] == s["label"] and len(s["_candidates"]) <= dp.DEFAULTS["name_candidates"]
        assert s["start"] <= s["_frame"] <= s["end"]
    assert [dp.public(s) for s in found] == [{k: x for k, x in s.items() if k[0] != "_"} for s in found]


def test_frames_naming_reads_its_own_vocabulary_embeddings():
    t, emb, vocab, _ = footage()
    swapped = vocab[[0, 2, 1, 3]]                    # the naming encoder "sees" the two products the other way round
    assert [s["label"] for s in _detect("frames", naming_vocabulary=swapped)] == ["botella", "aretes"]


def test_naming_frames_and_ranked_labels():
    score = np.array([0.1, 0.9, 0.5, 0.7, 2.0])
    assert list(dp.naming_frames(score, 0, 3, 2)) == [1, 3]           # frame 4 is outside the segment
    vocab = np.eye(3, dtype=np.float32)
    frames = np.array([[0.2, 1.0, 0.5]], np.float32)
    assert dp.ranked_labels(frames, vocab, ["a", "b", "c"], None, 2) == ["b", "c"]


def test_name_question_offers_clip_candidates_with_the_rules_answer():
    seg = {"label": "aretes", "confidence": 0.3, "_candidates": ["aretes", "collar", "aretes", "dije"]}
    q = dp.name_question(seg, "/f.jpg", "es")
    assert q.topic == "product_name" and q.kind == "choice" and q.image == "/f.jpg"
    assert q.values() == ("aretes", "collar", "dije") and q.default == ("aretes",) and q.confidence == 0.3
    assert dd.ROUTES["product_name"] == ("llm",) and dd.needs(q, dd.settings(None))
    assert "producto" in q.prompt and dp.name_question(seg, "/f.jpg", "en").prompt.startswith("Which product")
    assert dp.name_question({"label": "x", "confidence": 0.1}, "/f.jpg") is None


def test_apply_names_takes_the_models_answer_and_keeps_the_rest():
    segs = [{"label": "taza", "confidence": 0.2, "_candidates": ["taza", "botella"]},
            {"label": "gorra", "confidence": 0.2, "_candidates": ["gorra", "aretes"]},
            {"label": "dije", "confidence": 0.2}]
    named = dp.apply_names(segs, {0: "/a.jpg", 2: "/c.jpg"}, "es", FakeDecider({"product_name": "botella"}, 0.9))
    assert named[0]["label"] == "botella" and named[0]["confidence"] == 0.9 and named[0]["_named_by"] == "llm"
    assert named[1] == segs[1] and named[2] == segs[2]                      # no picture / no candidates
    assert dp.apply_names(segs, {0: "/a.jpg"}, "es", dd.rules_only) == segs


# ── application ──────────────────────────────────────────────────────────────

class _Encoder:
    def __init__(self):
        self.covers = []

    def cover(self, video, t, out):
        self.covers.append((Path(video).name, t))
        Path(out).write_bytes(b"jpg")
        return Path(out)


class _Detector(FakeDetector, FakeVisual):
    def __init__(self):
        FakeDetector.__init__(self)
        FakeVisual.__init__(self)
        self.english = []

    def english_text_embeddings(self, texts, settings):
        self.english += list(texts)
        return self.text_embeddings(texts, settings)


def _services(tmp_path, reasoner):
    written = {}
    projects = SimpleNamespace(write_json=lambda p, d: written.__setitem__(p.name, json.loads(json.dumps(d))))
    svc = SimpleNamespace(visual=_Detector(), projects=projects, recipes=YamlRecipeRepository(), cache=JsonCacheStore(),
                          encoder=_Encoder(), reasoner=reasoner, classifier=None)
    return svc, written


def _vision(svc, tmp_path, cfg, naming):
    _, _, _, an = footage()
    cfg = {**cfg, "vision": {"min_score": 0.5, "naming": naming, "label_temperature": 0.05}}   # CLIP unsure
    project = Project(dir=tmp_path, name="p", recipe="unboxing", sources=[{"path": "x.mov"}], cfg=cfg)
    return project, cfg, app.Vision(svc, project, [{"proxy": str(tmp_path / "p.mp4"), "id": "x"}], [an], cfg)


def test_decider_naming_asks_the_language_model_with_the_best_frame(tmp_path, cfg):
    def answer(prompt, keys):               # the second option ("b") for every product
        return ["b"], 0.9
    reasoner = FakeReasoner(answer)
    svc, _ = _services(tmp_path, reasoner)
    project, cfg, vision = _vision(svc, tmp_path, cfg, "decider")
    assert cfg.get("decider", {}).get("enabled") in (None, False)      # asking for it is enough
    found = vision.detect()
    named = app.name_products(svc, project, vision, found, cfg)
    assert len(reasoner.prompts) == len(found) == 2 and all(i is not None for i in reasoner.images)
    assert [n["label"] for n in named] == [f["_candidates"][1] for f in found]
    assert all(n["confidence"] == 0.9 for n in named)
    assert len(svc.encoder.covers) == 2 and svc.visual.released >= 1 and reasoner.released == 1
    assert any(x.endswith("held in a hand") for x in svc.visual.english)
    result = app.products_result(vision, named)
    assert all(not any(k.startswith("_") for k in s) for s in result["segments"])
    # a second run reads the cached answers: no new question
    app.name_products(svc, project, vision, vision.detect(), cfg)
    assert len(reasoner.prompts) == 2


def test_decider_naming_only_asks_when_clip_is_unsure(tmp_path, cfg):
    reasoner = FakeReasoner()
    svc, _ = _services(tmp_path, reasoner)
    project, cfg, vision = _vision(svc, tmp_path, cfg, "decider")
    found = [{**s, "confidence": 0.95} for s in vision.detect()]
    assert app.name_products(svc, project, vision, found, cfg) == found and not reasoner.prompts


def test_decider_naming_without_the_language_model_keeps_clips_names(tmp_path, cfg):
    reasoner = FakeReasoner(reason="Ollama is not running")
    svc, _ = _services(tmp_path, reasoner)
    project, cfg, vision = _vision(svc, tmp_path, cfg, "decider")
    found = vision.detect()
    assert app.name_products(svc, project, vision, found, cfg) == found and not reasoner.prompts


def test_clip_and_frames_naming_never_ask(tmp_path, cfg):
    for naming in ("clip", "frames"):
        reasoner = FakeReasoner()
        svc, _ = _services(tmp_path, reasoner)
        project, cfg2, vision = _vision(svc, tmp_path, cfg, naming)
        found = vision.detect()
        assert app.name_products(svc, project, vision, found, cfg2) is found and not reasoner.prompts
        assert not svc.encoder.covers
        assert bool(svc.visual.english) == (naming == "frames")


def test_find_products_writes_the_usual_format(tmp_path, cfg, monkeypatch):
    svc, written = _services(tmp_path, FakeReasoner(lambda p, k: (["b"], 0.95)))
    project, cfg2, _ = _vision(svc, tmp_path, cfg, "decider")
    _, _, _, an = footage()
    monkeypatch.setattr(app, "prepare_sources", lambda s, p, c: ([{"proxy": str(tmp_path / "p.mp4"), "id": "x"}], [an]))
    result = app.find_products(svc, project)
    assert set(written["products.json"]) == {"segments", "model", "language"}
    assert set(result["segments"][0]) == {"source", "start", "end", "best", "label", "confidence", "score"}


# ── adapters / aliases ───────────────────────────────────────────────────────

def test_english_text_embeddings_use_the_image_models_text_encoder(monkeypatch):
    det, seen = ClipVisualDetector(), []
    monkeypatch.setattr(det, "text_embeddings", lambda texts, s: seen.append(s["text_model"]) or np.zeros((1, 512)))
    det.english_text_embeddings(["a photo of a mug"], {"model": "my/clip"})
    det.english_text_embeddings(["a photo of a mug"], {})
    assert seen == ["my/clip", "sentence-transformers/clip-ViT-B-32"]


def test_spanish_aliases_of_the_naming_keys():
    got = legacy_es.config({"vision": {"nombres": "decididor", "fotogramas_nombre": 2, "candidatos_nombre": 4}})
    assert got["vision"] == {"naming": "decider", "name_frames": 2, "name_candidates": 4}
    assert legacy_es.config({"vision": {"nombres": "fotogramas"}})["vision"]["naming"] == "frames"
