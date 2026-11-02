"""Products shown in the footage, recognized from image–text embeddings (e.g. CLIP). Pure numpy.

Inputs come from a VisualDetector: per source, the times of the sampled frames and their unit
embeddings; texts (prompts, vocabulary) embedded in the same space. Nothing here loads a model.

  product_score   how much each frame looks like "a product shown to the camera":
                    prompts      similarity to positive prompts − similarity to negative prompts
                    objectness   the frame matches ONE object of the vocabulary much better than the
                                 rest (max − mean similarity): a recognizable product is in view
                    novelty      the frame differs from the usual view of that source (the table,
                                 the shipping bag…): something is being shown
                    sharpness    the analysis sharpness (blurry frames lose)
                  each part is standardized over all sources and weighted (vision.weights).
  segments        stretches of high-score frames (hysteresis: start above min_score, extend above
                  extend_score), cut where the image changes (cosine similarity between consecutive
                  frames < continuity), with min/max durations; the same product shown twice close
                  in time is merged; each segment gets the best sub-window (selection.best_start)
                  and a zero-shot label from the vocabulary.
  product plan    one shot per product (story order), a card per product and the most confident
                  ones as "stars" (fast → slow ramp + polaroid).
"""
from __future__ import annotations

import numpy as np

from .selection import best_start

# Same values as the `vision` section of recipes/base.yaml (a workspace base.yaml without that
# section still gets them; a test keeps both in sync).
DEFAULTS = {
    "enabled": "auto",
    "device": "cpu",
    "fps": 2,
    "positive": {"es": ["un producto sostenido en la mano, primer plano", "a product held in a hand, close-up",
                        "un accesorio pequeño mostrado a la cámara", "a small accessory shown to the camera"],
                 "en": ["a product held in a hand, close-up", "a small accessory shown to the camera"]},
    "negative": {"es": ["bolsa de envío de plástico gris", "a gray plastic shipping bag", "manos abriendo un paquete",
                        "hands opening a package", "imagen borrosa", "a blurry photo"],
                 "en": ["a gray plastic shipping bag", "hands opening a package", "a blurry photo"]},
    "weights": {"prompts": 0.5, "objectness": 1.0, "novelty": 1.0, "sharpness": 0.5},
    "smooth": 1.5,           # seconds of smoothing of the score
    "min_score": 0.35,       # standardized score where a product segment starts…
    "extend_score": -0.25,   # …and the score its neighbor frames need to extend it
    "continuity": 0.78,      # consecutive frames with a lower cosine similarity start a new segment
    "max_gap": 1.0,          # seconds of lower score bridged inside a segment
    "min_duration": 1.0,
    "max_duration": 25.0,
    "same_product": 0.82,    # two segments this similar (mean embeddings) are the same product…
    "same_product_within": 20.0,   # …if they are less than this many seconds apart (they are merged)
    "window": 2.0,           # duration of the best sub-window of each product
    "max_products": 15,
    "stars": 3,
    "vocabulary": None,
    "product_plan": False,
    "product_search": False,
    "label_temperature": 5.0,    # softmax temperature of the standardized label scores
}


def settings(vision_cfg: dict | None, language: str = "es") -> dict:
    """Vision settings with defaults (weights merged key by key; {es, en} values picked by language)."""
    v = dict(DEFAULTS)
    for k, x in (vision_cfg or {}).items():
        if x is None and k != "vocabulary":
            continue
        v[k] = {**DEFAULTS["weights"], **x} if k == "weights" and isinstance(x, dict) else x
    for k in ("positive", "negative"):
        if isinstance(v[k], dict):
            v[k] = v[k].get(language) or v[k].get("es") or []
    return v


# ── math helpers ─────────────────────────────────────────────────────────────

def unit(x) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def cosine(a, b) -> np.ndarray:
    """Cosine similarity matrix [len(a), len(b)]."""
    return unit(np.atleast_2d(a)) @ unit(np.atleast_2d(b)).T


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    """Moving average over n samples (edges use the available samples only)."""
    x = np.asarray(x, float)
    if n <= 1 or len(x) < 2:
        return x
    k = np.ones(n)
    return np.convolve(x, k, mode="same") / np.convolve(np.ones_like(x), k, mode="same")


def standardize(parts: list[np.ndarray]) -> list[np.ndarray]:
    """z-scores computed over the concatenation of several series (one per source)."""
    allv = np.concatenate([np.asarray(p, float) for p in parts]) if parts else np.zeros(0)
    if len(allv) == 0:
        return parts
    mu, sd = float(allv.mean()), float(allv.std())
    return [(np.asarray(p, float) - mu) / max(sd, 1e-6) for p in parts]


# ── frame features ───────────────────────────────────────────────────────────

def prompt_contrast(emb: np.ndarray, positive: np.ndarray, negative: np.ndarray) -> np.ndarray:
    """Mean similarity to the positive prompts − mean similarity to the negative ones."""
    pos = (emb @ positive.T).mean(1) if len(positive) else np.zeros(len(emb))
    neg = (emb @ negative.T).mean(1) if len(negative) else np.zeros(len(emb))
    return pos - neg


def objectness(emb: np.ndarray, vocabulary: np.ndarray) -> np.ndarray:
    """max − mean similarity to the vocabulary: one object clearly stands out."""
    if not len(vocabulary):
        return np.zeros(len(emb))
    s = emb @ vocabulary.T
    return s.max(1) - s.mean(1)


def novelty(emb: np.ndarray) -> np.ndarray:
    """1 − similarity to the median frame of the source (its usual view)."""
    if not len(emb):
        return np.zeros(0)
    return 1.0 - emb @ unit(np.median(emb, axis=0))


def sharpness_at(an: dict | None, times: np.ndarray) -> np.ndarray:
    """log sharpness of the analysis samples, interpolated at `times` (0 without analysis)."""
    m = (an or {}).get("samples") or []
    if len(m) < 2:
        return np.zeros(len(times))
    return np.interp(times, [x["t"] for x in m], np.log1p([x["sharpness"] for x in m]))


def product_scores(sources: list[dict], positive: np.ndarray, negative: np.ndarray, vocabulary: np.ndarray,
                   cfg: dict | None = None) -> list[np.ndarray]:
    """Product score per frame of every source (standardized over all sources, smoothed).

    sources: [{"t": times, "emb": unit embeddings [n, d], "analysis": analysis dict | None}, …]
    """
    v = settings(cfg)
    w = v["weights"]
    feats = {
        "prompts": [prompt_contrast(s["emb"], positive, negative) for s in sources],
        "objectness": [objectness(s["emb"], vocabulary) for s in sources],
        "novelty": [novelty(s["emb"]) for s in sources],
        "sharpness": [sharpness_at(s.get("analysis"), np.asarray(s["t"], float)) for s in sources],
    }
    total = [np.zeros(len(s["t"])) for s in sources]
    for name, parts in feats.items():
        weight = float(w.get(name, 0) or 0)
        if weight:
            for i, z in enumerate(standardize(parts)):
                total[i] = total[i] + weight * z
    out = []
    for s, x in zip(sources, total):
        n = max(int(round(float(v["smooth"]) * _rate(s["t"]))), 1)
        out.append(smooth(x, n))
    return standardize(out)


def text_scores(emb: np.ndarray, text: np.ndarray) -> np.ndarray:
    """Similarity of every frame to one text embedding (or the mean of several)."""
    return emb @ unit(np.atleast_2d(text)).mean(0)


def _rate(t) -> float:
    t = np.asarray(t, float)
    return 1.0 / float(np.median(np.diff(t))) if len(t) > 1 else 1.0


# ── segmentation ─────────────────────────────────────────────────────────────

def segment_products(times, emb: np.ndarray, score: np.ndarray, cfg: dict | None = None) -> list[dict]:
    """Stretches where a product is shown → [{"start", "end", "i0", "i1", "score"}] (i0..i1: frame indices).

    Hysteresis: a segment starts at a frame whose score exceeds `min_score` (best first) and grows to
    both sides over frames above `extend_score` while the image is continuous (cosine similarity
    between consecutive frames ≥ `continuity`); up to `max_gap` seconds of lower score are bridged.
    Segments never overlap, last at most `max_duration` and at least `min_duration`.
    Then nearby segments of the same product are merged (see merge_same_product).
    """
    v = settings(cfg)
    t = np.asarray(times, float)
    n = len(t)
    if n == 0:
        return []
    dt = 1.0 / _rate(t)
    cont = np.r_[1.0, np.sum(emb[1:] * emb[:-1], axis=1)] if n > 1 else np.ones(1)
    hi, lo = float(v["min_score"]), float(v["extend_score"])
    max_len = max(int(round(float(v["max_duration"]) / dt)), 1)
    max_miss = int(round(float(v["max_gap"]) / dt))
    taken = np.zeros(n, bool)
    segs = []
    for p in np.argsort(-score):
        if score[p] <= hi:
            break
        if taken[p]:
            continue
        lim = [p, p]
        for side, step in ((1, 1), (0, -1)):           # grow right, then left
            j, miss = p, 0
            while True:
                k = j + step
                if not 0 <= k < n or taken[k] or lim[1] - lim[0] + 1 >= max_len:
                    break
                if cont[max(j, k)] < v["continuity"]:      # the image changes between j and k
                    break
                if score[k] > lo:
                    lim[side], miss = k, 0
                else:
                    miss += 1
                    if miss > max_miss:
                        break
                j = k
        i0, i1 = lim
        taken[i0:i1 + 1] = True
        if t[i1] - t[i0] + dt >= float(v["min_duration"]):
            segs.append(_segment(t, score, i0, i1, dt))
    segs.sort(key=lambda x: x["i0"])
    return merge_same_product(segs, t, emb, score, v)


def _segment(t: np.ndarray, score: np.ndarray, i0: int, i1: int, dt: float) -> dict:
    return {"start": round(float(t[i0]), 2), "end": round(float(t[i1] + dt), 2), "i0": int(i0), "i1": int(i1),
            "score": round(float(score[i0:i1 + 1].mean()), 3)}


def mean_embedding(emb: np.ndarray, i0: int, i1: int) -> np.ndarray:
    return unit(emb[i0:i1 + 1].mean(0))


def merge_same_product(segs: list[dict], times, emb: np.ndarray, score: np.ndarray,
                       cfg: dict | None = None) -> list[dict]:
    """Consecutive segments showing the same product are joined into one.

    Same product = mean embeddings with cosine similarity ≥ `same_product` and less than
    `same_product_within` seconds between them (e.g. the box first, then the product out of it),
    as long as the joined segment is not longer than `max_duration`.
    """
    v = settings(cfg)
    t = np.asarray(times, float)
    dt = 1.0 / _rate(t)
    out: list[dict] = []
    for s in sorted(segs, key=lambda x: x["i0"]):
        if out:
            p = out[-1]
            near = (s["start"] - p["end"] <= float(v["same_product_within"])
                    and s["end"] - p["start"] <= float(v["max_duration"]))
            if near and float(mean_embedding(emb, p["i0"], p["i1"]) @ mean_embedding(emb, s["i0"], s["i1"])) \
                    >= float(v["same_product"]):
                out[-1] = _segment(t, score, p["i0"], s["i1"], dt)
                continue
        out.append(s)
    return out


def label_baseline(emb: np.ndarray, vocabulary: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(median, std) of the similarity of every vocabulary entry over all frames of a source.

    Things visible all the time (the hands, the table, the shipping bag) match some labels in every
    frame ("press-on nails" for manicured hands…); comparing with this baseline removes that bias.
    """
    s = emb @ vocabulary.T
    return np.median(s, axis=0), np.maximum(s.std(axis=0), 1e-6)


def zero_shot_label(frames: np.ndarray, vocabulary: np.ndarray, labels: list[str],
                    baseline: tuple[np.ndarray, np.ndarray] | None = None,
                    temperature: float = 2.0) -> tuple[str, float]:
    """(label, confidence) of the object shown in `frames` (embeddings [n, d] or one [d]).

    Each label scores its mean similarity, standardized with the source `baseline` when given;
    confidence = softmax margin between the two best labels (0 = undecided, 1 = sure).
    """
    if not len(labels):
        return "", 0.0
    sims = (np.atleast_2d(frames) @ vocabulary.T).mean(0)
    if baseline is not None:
        sims = (sims - baseline[0]) / baseline[1]
    logits = temperature * sims
    p = np.exp(logits - logits.max())
    p /= p.sum()
    order = np.argsort(-p)
    margin = p[order[0]] - (p[order[1]] if len(order) > 1 else 0.0)
    return labels[int(order[0])], round(float(margin), 3)


def detect_products(sources: list[dict], positive: np.ndarray, negative: np.ndarray, vocabulary: np.ndarray,
                    labels: list[str], cfg: dict | None = None) -> list[dict]:
    """Products of every source, in story order.

    → [{"source", "start", "end", "best": [a, b], "label", "confidence", "score"}, …]
    `vocabulary` (embeddings) and `labels` (names in the output language) are aligned.
    """
    v = settings(cfg)
    scores = product_scores(sources, positive, negative, vocabulary, v)
    found = []
    for si, (src, sc) in enumerate(zip(sources, scores)):
        t = np.asarray(src["t"], float)
        if len(t) < 2:
            continue
        segs = segment_products(t, src["emb"], sc, v)
        baseline = label_baseline(src["emb"], vocabulary) if len(vocabulary) else None
        for s in segs:
            dur = min(float(v["window"]), s["end"] - s["start"])
            inside = (t >= s["start"]) & (t <= s["end"])
            a = best_start(t[inside], sc[inside], s["start"], s["end"], dur)
            best = [round(a, 2), round(min(a + dur, s["end"]), 2)]
            sel = (t >= best[0]) & (t <= best[1])
            label, conf = zero_shot_label(src["emb"][s["i0"]:s["i1"] + 1], vocabulary, labels, baseline,
                                          float(v["label_temperature"]))
            found.append({"source": si, "start": s["start"], "end": s["end"], "best": best, "label": label,
                          "confidence": conf, "score": round(float(sc[sel].mean() if sel.any() else s["score"]), 3)})
    return sorted(found, key=lambda x: (x["source"], x["start"]))


# ── automatic plan with one shot per product ─────────────────────────────────

STAR_FAST_SPEED = 1.8
STAR_SLOW_SPEED = 0.6
STAR_SLOW_SECONDS = 0.85


def product_plan(products: list[dict], cfg: dict, opening: tuple[int, float, float] | None = None) -> dict:
    """Edit plan of an unboxing: hook → (opening) → one shot per product → collage.

    At most `max_products` (the best-scoring ones). The `stars` products with the highest confidence get a fast → slow ramp and a polaroid with
    their name. Every product gets a card with its proposed name and a counter (n/N).
    opening: optional (source, start, end) shown before the first product (the package being opened).
    """
    v = settings(cfg.get("vision"))
    limit = int(v["max_products"] or 0)
    if limit and len(products) > limit:                   # the best-scoring ones, still in story order
        keep = sorted(range(len(products)), key=lambda k: -products[k]["score"])[:limit]
        products = [products[k] for k in sorted(keep)]
    rot = cfg["transitions"]["rotation"] or ["cut"]
    hook_cfg = cfg["hook"]
    n_stars = int(v["stars"] or 0)
    ranked = sorted(range(len(products)), key=lambda k: (-products[k]["confidence"], -products[k]["score"]))
    stars = set(ranked[:n_stars]) if len(products) > 1 else set()
    shots: list[dict] = []
    plan_texts: list[dict] = []

    if products and hook_cfg["strategy"] == "teaser":
        top = products[max(range(len(products)), key=lambda k: (products[k]["score"], products[k]["confidence"]))]
        a, b = top["best"]
        shots.append({"source": top["source"], "start": a, "end": round(min(b, a + float(hook_cfg["duration"])), 2),
                      "speed": 1.0, "role": "hook", "transition": "cut", "effects": [{"type": "zoom_punch"}],
                      "note": top["label"]})
    if opening:
        si, a, b = opening
        shots.append({"source": si, "start": a, "end": b, "speed": cfg["pacing"]["speed_without_voice"],
                      "role": "opening", "transition": "cut", "effects": [], "note": "opening"})
    collage_from: list[int] = []
    k_rot = 0
    for n, p in enumerate(products, start=1):
        a, b = p["best"]
        first = len(shots)
        transition = rot[k_rot % len(rot)]
        k_rot += 1
        title = p["label"][:1].upper() + p["label"][1:] if p["label"] else f"#{n}"
        if (n - 1) in stars and b - a > STAR_SLOW_SECONDS + 0.5:
            mid = round(b - STAR_SLOW_SECONDS, 2)
            group = f"star {n}"
            shots.append({"source": p["source"], "start": a, "end": mid, "speed": STAR_FAST_SPEED, "role": "product",
                          "transition": "zoom", "effects": [], "group": group,
                          "note": f"{p['label']} ({p['confidence']:.2f})"})
            shots.append({"source": p["source"], "start": mid, "end": b, "speed": STAR_SLOW_SPEED, "role": "star",
                          "transition": "cut", "continuous": True, "group": group,
                          "effects": [{"type": "zoom_punch", "t": 0.05, "scale": 1.1}]})
            shots.append({"type": "polaroid", "from_shot": len(shots) - 1, "text": title, "tilt": -3,
                          "transition": "cut", "role": "polaroid", "group": group})
            last_video = len(shots) - 2
        else:
            shots.append({"source": p["source"], "start": a, "end": b, "speed": 1.0, "role": "product",
                          "transition": transition, "effects": [{"type": "beat_pulse"}],
                          "note": f"{p['label']} ({p['confidence']:.2f})"})
            last_video = len(shots) - 1
        collage_from.append(last_video)
        plan_texts.append({"text": title, "shot": first, "until_shot": last_video, "style": "card"})
        plan_texts.append({"text": f"{n}/{len(products)}", "shot": first, "until_shot": last_video,
                           "style": "counter"})
    if len(collage_from) >= 3:
        shots.append({"type": "collage", "from_shots": collage_from, "background_from": collage_from[-1],
                      "transition": "zoom", "role": "collage"})
    if len(shots) > 1 and hook_cfg["strategy"] == "teaser" and shots[0]["role"] == "hook":
        shots[1]["transition"] = hook_cfg.get("transition", "flash")
    if hook_cfg.get("text") and shots:
        plan_texts.insert(0, {"text": hook_cfg["text"], "shot": 0, "style": "hook"})
    return {
        "note": "Editable plan (one shot per detected product). Shot times = seconds inside each source; "
                "check the proposed product names in the cards. Render again with: sve render <project>",
        "shots": shots,
        "texts": plan_texts,
    }
