"""Products recognized with a vision model: pure domain logic with synthetic embeddings (no model)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from social_video_editor.adapters.clip.detector import ClipVisualDetector
from social_video_editor.adapters.files import legacy_es
from social_video_editor.application import products as app
from social_video_editor.cli import parser
from social_video_editor.domain import products as dp
from social_video_editor.domain.models import Project, UserError
from social_video_editor.domain.pieces import Sequence
from social_video_editor.domain.selection import best_window, resolve_ranges
from social_video_editor.domain.timeline import absolute_texts, compute_timeline

D = 16


def axis(k: int, noise: float = 0.0, rng=None) -> np.ndarray:
    v = np.zeros(D, np.float32)
    v[k] = 1.0
    if noise:
        v = v + noise * rng.standard_normal(D).astype(np.float32)
    return dp.unit(v)


def footage(seed: int = 0):
    """60 s at 2 fps: background (axis 0) with product A (axis 1) at 10–16 s and product B (axis 2) at
    30–35 s; vocabulary = [mug, A, B, hat] (the background is none of them)."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, 60, 0.5)
    emb = np.stack([axis(0, 0.05, rng) for _ in t])
    for a, b, k in ((10, 16, 1), (30, 35, 2)):
        sel = (t >= a) & (t < b)
        emb[sel] = np.stack([dp.unit(0.3 * axis(0) + axis(k, 0.05, rng)) for _ in range(sel.sum())])
    vocab = np.stack([axis(3), axis(1), axis(2), axis(4)])
    an = {"duration": 60.0, "samples": [{"t": float(x), "sharpness": 50.0, "brightness": 0.5, "motion": 1.0,
                                         "color": 10.0} for x in np.arange(0, 60, 0.25)]}
    return t, emb, vocab, an


# ── settings / math ──────────────────────────────────────────────────────────

def test_settings_defaults_localized_and_in_sync_with_base_recipe(cfg):
    v = dp.settings(None, "en")
    assert v["positive"] == dp.DEFAULTS["positive"]["en"] and v["min_score"] == dp.DEFAULTS["min_score"]
    assert dp.settings({"weights": {"novelty": 0}})["weights"]["objectness"] == 1.0      # merged key by key
    assert dp.settings({"positive": ["x"], "vocabulary": None})["positive"] == ["x"]
    # recipes/base.yaml documents the same defaults
    base = dict(cfg["vision"])
    base.pop("model"), base.pop("text_model")
    defaults = dp.settings(None, cfg["language"])
    assert {k: defaults[k] for k in base} == base


def test_cosine_smooth_standardize():
    a = np.array([[3.0, 0], [0, 2.0]])
    assert np.allclose(dp.cosine(a, a), np.eye(2))
    assert np.allclose(dp.smooth(np.array([0, 3.0, 0]), 3), [1.5, 1.0, 1.5])     # edges average what exists
    z = dp.standardize([np.array([1.0, 2.0]), np.array([3.0])])
    assert np.isclose(np.concatenate(z).mean(), 0) and np.isclose(np.concatenate(z).std(), 1)


def test_features_objectness_novelty_prompts():
    t, emb, vocab, an = footage()
    obj, nov = dp.objectness(emb, vocab), dp.novelty(emb)
    product = ((t >= 10) & (t < 16)) | ((t >= 30) & (t < 35))
    assert nov[product].min() > nov[~product].max()
    assert obj[product].mean() > obj[~product].mean() - 1e-6
    pc = dp.prompt_contrast(emb, np.stack([axis(1), axis(2)]), np.stack([axis(0)]))
    assert pc[product].min() > pc[~product].max()
    assert np.all(dp.sharpness_at(None, t) == 0)


# ── segmentation ─────────────────────────────────────────────────────────────

def test_segments_split_where_the_image_changes_and_bridge_short_dips():
    t = np.arange(0, 20, 0.5)
    emb = np.stack([axis(1)] * 20 + [axis(2)] * 20)      # the image changes at 10 s
    score = np.ones(len(t))
    score[6] = -1.0                                        # a 0.5 s dip inside the first product
    segs = dp.segment_products(t, emb, score, {"min_score": 0.5, "extend_score": 0.0, "continuity": 0.8})
    assert [(s["start"], s["end"]) for s in segs] == [(0.0, 10.0), (10.0, 20.0)]


def test_segments_min_and_max_duration():
    t = np.arange(0, 40, 0.5)
    emb = np.stack([axis(1)] * len(t))
    score = np.zeros(len(t))
    score[4] = 2.0                                         # an isolated 0.5 s blip: too short
    score[20:] = 1.0                                       # 10–40 s: longer than max_duration
    v = {"min_score": 0.5, "extend_score": 0.5, "min_duration": 1.0, "max_duration": 8.0, "same_product": 2}
    segs = dp.segment_products(t, emb, score, v)
    assert all(s["end"] - s["start"] <= 8.0 + 1e-6 for s in segs)
    assert all(s["start"] >= 10.0 for s in segs) and len(segs) >= 4
    covered = sum(s["end"] - s["start"] for s in segs)
    assert covered == pytest.approx(30.0)                                  # 10–40 s, without overlaps
    assert [s["i0"] for s in segs] == sorted(s["i0"] for s in segs)


def test_same_product_close_in_time_is_merged_but_not_a_different_one():
    t = np.arange(0, 60, 0.5)
    emb = np.stack([axis(0)] * len(t))
    for a, b, k in ((5, 9, 1), (15, 19, 1), (40, 44, 1), (22, 26, 2)):
        emb[(t >= a) & (t < b)] = axis(k)
    score = np.where(np.all(emb == axis(0), axis=1), -2.0, 1.0)
    segs = dp.segment_products(t, emb, score, {"min_score": 0.5, "extend_score": 0.5, "same_product_within": 10})
    spans = [(s["start"], s["end"]) for s in segs]
    assert spans == [(5.0, 19.0), (22.0, 26.0), (40.0, 44.0)]      # 5–9 + 15–19 merged; 40 s is too far


def test_zero_shot_label_with_baseline_removes_ever_present_bias():
    vocab = np.stack([axis(0), axis(1), axis(2)])
    labels = ["hands", "earrings", "bottle"]
    # every frame shows "hands" a lot; the product frames add a bit of "earrings"
    background = np.stack([dp.unit(axis(0) + 0.1 * axis(1) + 0.1 * axis(2)) for _ in range(20)])
    product = np.stack([dp.unit(axis(0) + 0.5 * axis(1)) for _ in range(4)])
    assert dp.zero_shot_label(product, vocab, labels)[0] == "hands"
    base = dp.label_baseline(np.concatenate([background, product]), vocab)
    label, conf = dp.zero_shot_label(product, vocab, labels, base)
    assert label == "earrings" and 0 < conf <= 1
    assert dp.zero_shot_label(product[0], vocab, [], base) == ("", 0.0)


def test_detect_products_end_to_end_synthetic():
    t, emb, vocab, an = footage()
    sources = [{"t": t, "emb": emb, "analysis": an}]
    found = dp.detect_products(sources, np.zeros((0, D)), np.zeros((0, D)), vocab,
                               ["mug", "earrings", "bottle", "hat"], {"min_score": 0.5})
    assert [f["label"] for f in found] == ["earrings", "bottle"]
    (a, b), (c, d) = [(f["start"], f["end"]) for f in found]
    assert 9 <= a <= 10.5 and 15 <= b <= 17 and 29 <= c <= 30.5 and 34 <= d <= 36
    for f in found:
        assert set(f) == {"source", "start", "end", "best", "label", "confidence", "score"}
        assert f["start"] <= f["best"][0] < f["best"][1] <= f["end"] and f["best"][1] - f["best"][0] <= 2.0 + 1e-6
    assert dp.detect_products([{"t": np.zeros(0), "emb": np.zeros((0, D)), "analysis": None}],
                              np.zeros((0, D)), np.zeros((0, D)), vocab, ["a", "b", "c", "d"]) == []


# ── plan ─────────────────────────────────────────────────────────────────────

def _products(n=5):
    return [{"source": 0, "start": 10.0 * k, "end": 10.0 * k + 4, "best": [10.0 * k + 1, 10.0 * k + 3],
             "label": f"item {k}", "confidence": [0.2, 0.9, 0.1, 0.8, 0.7][k % 5], "score": 1.0 + k / 10}
            for k in range(n)]


def test_product_plan_shape(cfg):
    plan = dp.product_plan(_products(), cfg, opening=(0, 0.5, 3.0))
    shots, texts = plan["shots"], plan["texts"]
    roles = [s.get("role") for s in shots]
    assert roles[:2] == ["hook", "opening"] and roles[-1] == "collage"
    assert roles.count("star") == 3 and roles.count("polaroid") == 3          # top-3 confidence
    for k, s in enumerate(shots):
        if s.get("role") == "star":
            fast, pol = shots[k - 1], shots[k + 1]
            assert fast["speed"] > 1 > s["speed"] and s["continuous"] and fast["end"] == s["start"]
            assert pol["type"] == "polaroid" and pol["from_shot"] == k
            assert fast["group"] == s["group"] == pol["group"]
            assert pol["text"] in ("Item 1", "Item 3", "Item 4")
    cards = [x for x in texts if x["style"] == "card"]
    counters = [x["text"] for x in texts if x["style"] == "counter"]
    assert len(cards) == 5 and counters == [f"{k}/5" for k in range(1, 6)]
    assert all(c["until_shot"] >= c["shot"] for c in cards)
    collage = shots[-1]
    assert len(collage["from_shots"]) == 5 and all(shots[i].get("source") == 0 for i in collage["from_shots"])
    # the plan is valid for the rest of the engine
    seq = Sequence.from_plan(plan)
    assert len(seq.render_order()) == len(shots)
    tl = compute_timeline(seq, cfg)
    assert tl.total > 5 and len(absolute_texts(plan, tl)) == len(texts)


def test_product_plan_limit_keeps_story_order_and_spanish_plan_translates(cfg):
    cfg = {**cfg, "vision": {"max_products": 2, "stars": 0}}
    plan = dp.product_plan(_products(4), cfg)
    starts = [s["start"] for s in plan["shots"] if s.get("role") == "product"]
    assert starts == [21.0, 31.0] and not any(s.get("role") == "star" for s in plan["shots"])
    assert "collage" not in [s.get("role") for s in plan["shots"]]        # fewer than 3 products


# ── search by text / product criterion ──────────────────────────────────────

def test_best_window_with_visual_series_and_text_search():
    t, emb, vocab, an = footage()
    series = {"t": t, "score": dp.text_scores(emb, axis(2))}
    a, b = best_window(an, [0, 60], 2.0, "product", series)
    assert 30 <= a and b <= 35.5
    # restricted range: the best inside it
    a, b = best_window(an, [5, 25], 2.0, "product", series)
    assert 5 <= a and b <= 25

    plan = {"shots": [{"source": 0, "search": {"text": "bottle", "in": [20, 50], "dur": 1.5}},
                      {"source": 0, "search": {"range": [0, 5], "dur": 1.0}}]}
    calls = []

    def visual(source, search):
        calls.append(search)
        return series if search.get("text") else None
    resolve_ranges(plan, [an], visual)
    s0, s1 = plan["shots"]
    assert 30 <= s0["start"] and s0["end"] <= 35.5 and s0["end"] - s0["start"] == pytest.approx(1.5)
    assert 0 <= s1["start"] and s1["end"] <= 5 and len(calls) == 2
    # without vision the text search falls back to the heuristic on the whole source
    plan = {"shots": [{"source": 0, "search": {"text": "bottle"}}]}
    resolve_ranges(plan, [an])
    assert 0 <= plan["shots"][0]["start"] < plan["shots"][0]["end"] <= 60


def test_spanish_search_text_is_translated():
    p = legacy_es.plan({"tomas": [{"fuente": 0, "buscar": {"texto": "funda de celular", "en": [40, 60]}}]})
    assert p["shots"][0]["search"] == {"text": "funda de celular", "range": [40, 60]}
    c = legacy_es.config({"visión": {"activado": True, "plan_productos": True, "pesos": {"novedad": 0.2}}})
    assert c == {"vision": {"enabled": True, "product_plan": True, "weights": {"novelty": 0.2}}}


# ── application with a fake detector ─────────────────────────────────────────

class FakeDetector:
    """Embeds the synthetic footage; texts are mapped to axes by keyword."""

    def __init__(self, reason=None):
        self.reason, self.texts_seen = reason, []

    def ready(self, settings, download=False):
        return self.reason

    def model_name(self, settings):
        return "fake-clip"

    def frame_embeddings(self, video, cache_dir, key, settings):
        t, emb, _, _ = footage()
        return t, emb

    def text_embeddings(self, texts, settings):
        self.texts_seen += list(texts)
        keys = {"earrings": 1, "bottle": 2, "botella": 2}
        out = [next((axis(k) for w, k in keys.items() if w in x), axis(3 + i % (D - 3))) for i, x in enumerate(texts)]
        # (other texts land on axes 3…15, never on the background axis 0)
        return np.stack(out)


class FakeProjects:
    def __init__(self):
        self.written = {}

    def write_json(self, path, data):
        self.written[path.name] = json.loads(json.dumps(data))


class FakeServices:
    def __init__(self, detector):
        self.visual, self.projects = detector, FakeProjects()


def _project(tmp_path, cfg):
    return Project(dir=tmp_path, name="p", recipe="unboxing", sources=[{"path": "x.mov"}], cfg=cfg)


def test_vision_detect_products_json_and_table(tmp_path, cfg):
    svc = FakeServices(FakeDetector())
    _, _, _, an = footage()
    cfg = {**cfg, "vision": {"min_score": 0.5}}
    vision = app.Vision(svc, _project(tmp_path, cfg), [{"proxy": "p.mp4", "id": "x"}], [an], cfg)
    found = vision.detect()
    result = app.products_result(vision, found)
    assert set(result) == {"segments", "model", "language"} and result["model"] == "fake-clip"
    assert [s["label"] for s in result["segments"]] == ["aretes", "botella"]      # English vocabulary, Spanish labels
    assert any(x.startswith("a photo of ") for x in svc.visual.texts_seen)
    lines = app.product_lines(result)
    assert lines[0].startswith("2 products (fake-clip, es)") and "aretes" in lines[2] and "botella" in lines[3]
    series = vision.search_series(0, {"text": "botella"})
    assert len(series["t"]) == len(series["score"])


def test_packaged_vocabulary_is_aligned_across_languages():
    es, en = app.packaged_vocabulary("es"), app.packaged_vocabulary("en")
    assert len(es) == len(en) >= 75 and len(set(es)) == len(es) and len(set(en)) == len(en)
    for pair in (("aretes", "earrings"), ("funda de celular", "phone case"), ("bolígrafos", "pens"),
                 ("audífonos", "headphones"), ("botella", "bottle")):
        assert en[es.index(pair[0])] == pair[1]


def test_vision_available_rules(cfg):
    assert app.vision_available(FakeServices(FakeDetector()), cfg)
    assert not app.vision_available(FakeServices(FakeDetector("not downloaded")), cfg)
    assert not app.vision_available(FakeServices(FakeDetector()), {**cfg, "vision": {"enabled": False}})
    with pytest.raises(UserError, match="not installed"):
        app.vision_available(FakeServices(FakeDetector("not installed")), cfg, explicit=True)
    no_visual = FakeServices(None)
    assert not app.vision_available(no_visual, cfg)


def test_visual_search_only_when_needed(tmp_path, cfg):
    svc = FakeServices(FakeDetector())
    _, _, _, an = footage()
    project = _project(tmp_path, cfg)
    infos = [{"proxy": "p.mp4", "id": "x"}]
    assert app.visual_search(svc, project, infos, [an], {"shots": [{"source": 0, "start": 0, "end": 1}]}, cfg) is None
    product_plan = {"shots": [{"source": 0, "search": {"range": [0, 9]}}]}
    assert app.visual_search(svc, project, infos, [an], product_plan, cfg) is None      # product_search: false
    cb = app.visual_search(svc, project, infos, [an], product_plan, {**cfg, "vision": {"product_search": True}})
    assert cb is not None and cb(0, {"range": [0, 9]}) is not None
    text_plan = {"shots": [{"source": 0, "search": {"text": "bottle"}}]}
    cb = app.visual_search(svc, project, infos, [an], text_plan, cfg)
    assert cb(0, {"text": "bottle"}) is not None and cb(0, {"range": [0, 9]}) is None


def test_product_plan_or_none(tmp_path, cfg):
    svc = FakeServices(FakeDetector())
    _, _, _, an = footage()
    project = _project(tmp_path, cfg)
    infos = [{"proxy": "p.mp4", "id": "x"}]
    assert app.product_plan_or_none(svc, project, infos, [an], cfg) is None            # base: product_plan off
    on = {**cfg, "vision": {"product_plan": True, "min_score": 0.5}}
    plan = app.product_plan_or_none(svc, project, infos, [an], on)
    assert plan is not None and "products.json" in svc.projects.written
    assert [x["text"] for x in plan["texts"] if x["style"] == "card"] == ["Aretes", "Botella"]
    assert plan["shots"][1]["role"] == "opening"                       # the footage before the first product


# ── adapter without the model / CLI ──────────────────────────────────────────

def test_clip_adapter_reports_missing_package_or_model(tmp_path, monkeypatch):
    import importlib.util
    det = ClipVisualDetector(cache_folder=str(tmp_path))
    assert "not downloaded" in det.ready({})                          # empty model cache: no torch import
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert "pip install 'social-video-editor[vision]'" in det.ready({})
    assert det.model_name({}) == "clip-ViT-B-32"


def test_cli_products_command_and_alias():
    for name in ("products", "productos"):
        a = parser().parse_args([name, "my_project"])
        assert a.project == "my_project" and a.f.__name__ == "cmd_products"
