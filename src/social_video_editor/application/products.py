"""Use cases with the local vision model (CLIP): find the products shown in the footage.

  sve products <project>   → table of products + cache/products.json
  plan (unboxing recipe)   → one shot per detected product (see domain.products.product_plan)
  render                   → "search": {"text": "phone case", "range": [40, 60]} uses the vision model;
                             the "product" criterion too when vision.product_search is true

The vision model is optional (`vision.enabled`): auto = used when installed and already downloaded;
true = may download it (≈1.2 GB, only once); false = never. `sve products` always may download it.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..domain import products as dp
from ..domain.models import Project, UserError
from ..domain.timebase import mmss
from ..log import log
from .analyze import prepare_sources
from .services import Services

LABEL_TEMPLATES = {"es": "una foto de {}", "en": "a photo of {}"}
NAMING_TEMPLATE = "a photo of {} held in a hand"     # vision.naming frames / decider (packaged vocabulary)
FRAMES = "product_frames"                          # in the project cache: the frame each product is named from


def vision_available(svc: Services, cfg: dict, explicit: bool = False) -> bool:
    """Whether the vision model can be used now (logs why not, when it matters)."""
    v = dp.settings(cfg.get("vision"), cfg.get("language", "es"))
    enabled = v["enabled"]
    if svc.visual is None or (enabled is False and not explicit):
        return False
    reason = svc.visual.ready(v, download=explicit or enabled is True)
    if reason:
        if explicit:
            raise UserError(f"Vision is not available: {reason}")
        log(f"Vision not used: {reason}")
        return False
    return True


class Vision:
    """Embeddings of a project's sources and of the prompts/vocabulary (computed on demand)."""

    def __init__(self, svc: Services, project: Project, infos: list[dict], analyses: list[dict], cfg: dict):
        self.svc, self.project, self.infos, self.analyses = svc, project, infos, analyses
        self.language = cfg.get("language", "es")
        self.v = dp.settings(cfg.get("vision"), self.language)
        self._sources: dict[int, dict] = {}

    def source(self, i: int) -> dict:
        if i not in self._sources:
            info = self.infos[i]
            t, emb = self.svc.visual.frame_embeddings(info["proxy"], self.project.cache, info["id"], self.v)
            self._sources[i] = {"t": np.asarray(t, float), "emb": np.asarray(emb, np.float32),
                                "analysis": self.analyses[i]}
        return self._sources[i]

    def texts(self, items: list[str]) -> np.ndarray:
        return np.asarray(self.svc.visual.text_embeddings(list(items), self.v), np.float32)

    def prompts(self) -> tuple[np.ndarray, np.ndarray]:
        return self.texts(self.v.get("positive") or []), self.texts(self.v.get("negative") or [])

    def vocabulary(self) -> tuple[np.ndarray, list[str]]:
        """(embeddings, labels in the output language).

        The packaged vocabulary is embedded in English (CLIP was trained on English captions; the
        multilingual text model is most precise there) and labeled in the project language.
        A custom `vision.vocabulary` list is embedded as written.
        """
        custom = self.v.get("vocabulary")
        if custom:
            tpl = LABEL_TEMPLATES.get(self.language, "{}")
            return self.texts([tpl.format(x) for x in custom]), list(custom)
        english = self.svc.recipes.vocabulary("en")
        labels = self.svc.recipes.vocabulary(self.language) if self.language != "en" else english
        return self.texts([LABEL_TEMPLATES["en"].format(x) for x in english]), labels

    def naming_vocabulary(self) -> np.ndarray | None:
        """The vocabulary embedded for naming (vision.naming frames / decider): the packaged English one
        with the image model's own text encoder; a custom vocabulary keeps the multilingual model (None)."""
        if dp.naming_mode(self.v) == "clip" or self.v.get("vocabulary"):
            return None
        english = self.svc.recipes.vocabulary("en")
        return np.asarray(self.svc.visual.english_text_embeddings([NAMING_TEMPLATE.format(x) for x in english],
                                                                  self.v), np.float32)

    def detect(self) -> list[dict]:
        sources = [self.source(i) for i in range(len(self.infos))]
        pos, neg = self.prompts()
        vocab, labels = self.vocabulary()
        packaging = self.texts(self.v["packaging"]) if self.v.get("unpacked") and self.v.get("packaging") else None
        return dp.detect_products(sources, pos, neg, vocab, labels, self.v, packaging, self.naming_vocabulary())

    def search_series(self, source: int, search: dict) -> dict | None:
        """Vision score series of one source for a plan "search" (text similarity or product score)."""
        src = self.source(source)
        if search.get("text"):
            score = dp.text_scores(src["emb"], self.texts([str(search["text"])]))
        else:
            pos, neg = self.prompts()
            vocab, _ = self.vocabulary()
            score = dp.product_scores([src], pos, neg, vocab, self.v)[0]
        return {"t": src["t"], "score": score}


def products_result(vision: Vision, segments: list[dict]) -> dict:
    """The content of cache/products.json."""
    return {"segments": [dp.public(s) for s in segments], "model": vision.svc.visual.model_name(vision.v),
            "language": vision.language}


def name_products(svc: Services, project: Project, vision: Vision, segments: list[dict], cfg: dict) -> list[dict]:
    """vision.naming: decider → the decider's language model looks at each product's best frame and
    picks among CLIP's best names (application/decide.py; cached answers). Other modes: unchanged.

    Asking for it is enough: the decider is used even when `decider.enabled` is false (the default);
    without the language model (no server, `decider.llm: false`) the CLIP names stand."""
    if dp.naming_mode(vision.v) != "decider" or not segments:
        return segments
    from .decide import run_decided

    images: dict[int, str] = {}
    for k, s in enumerate(segments):
        if "_frame" not in s:
            continue
        info = vision.infos[s["source"]]
        out = svc.cache.file(project.cache, f"{FRAMES}/{info['id']}_{float(s['_frame']):.2f}.jpg")
        try:
            images[k] = str(out if svc.cache.has(out) else svc.encoder.cover(Path(info["proxy"]), float(s["_frame"]), out))
        except (RuntimeError, OSError, ValueError) as e:
            log(f"Vision: no frame of product {k + 1} to look at ({e}); its CLIP name stays")
    if not images:
        return segments
    decider = dict(cfg.get("decider") or {})
    if decider.get("enabled") in (None, False) or str(decider.get("enabled")).strip().lower() in ("false", "no", "off"):
        decider["enabled"] = "auto"
    named = run_decided(svc, project, lambda decide: dp.apply_names(segments, images, vision.language, decide),
                        {**cfg, "decider": decider})
    changed = sum(1 for a, b in zip(segments, named) if a["label"] != b["label"])
    asked = sum(1 for s in named if s.get("_named_by"))
    if asked:
        log(f"Vision: the language model named {asked} product(s) ({changed} name(s) changed)")
    return named


def find_products(svc: Services, project: Project) -> dict:
    """Detects the products of every source, writes cache/products.json and returns its content."""
    cfg = project.cfg
    infos, analyses = prepare_sources(svc, project, cfg)
    vision_available(svc, cfg, explicit=True)
    vision = Vision(svc, project, infos, analyses, cfg)
    result = products_result(vision, name_products(svc, project, vision, vision.detect(), cfg))
    svc.projects.write_json(project.cache / "products.json", result)
    return result


def product_lines(result: dict) -> list[str]:
    """Compact text table of the detected products."""
    segs = result["segments"]
    lines = [f"{len(segs)} products ({result['model']}, {result['language']})",
             f"{'n':>3}  {'src':>3}  {'start–end':<13}  {'best window':<13}  {'label':<24} {'conf':>5} {'score':>6}"]
    for n, s in enumerate(segs, start=1):
        lines.append(f"{n:>3}  {s['source']:>3}  {mmss(s['start'])[:5]}–{mmss(s['end'])[:5]}  "
                     f"{mmss(s['best'][0])[:5]}–{mmss(s['best'][1])[:5]}  {s['label'][:24]:<24} "
                     f"{s['confidence']:>5.2f} {s['score']:>6.2f}")
    return lines


def visual_search(svc: Services, project: Project, infos: list[dict], analyses: list[dict], plan: dict,
                  cfg: dict) -> Callable[[int, dict], dict | None] | None:
    """Callback for selection.resolve_ranges when the plan has searches that vision can improve."""
    searches = [s["search"] for s in plan.get("shots", []) if s.get("search")]
    wants_text = any(b.get("text") for b in searches)
    wants_product = bool(dp.settings(cfg.get("vision"))["product_search"]) and any(
        b.get("criterion", "product") == "product" and not b.get("text") for b in searches)
    if not (wants_text or wants_product):
        return None
    if not vision_available(svc, cfg):
        if wants_text:
            log("Warning: \"search\" by text needs the vision model; using the product heuristic instead.")
        return None
    vision = Vision(svc, project, infos, analyses, cfg)

    def series(source: int, search: dict) -> dict | None:
        return vision.search_series(source, search) if search.get("text") or wants_product else None
    return series


def product_plan_or_none(svc: Services, project: Project, infos: list[dict], analyses: list[dict],
                         cfg: dict) -> dict | None:
    """Plan with one shot per detected product (recipes with vision.product_plan), or None."""
    from ..domain.selection import best_window

    if not dp.settings(cfg.get("vision"))["product_plan"] or not vision_available(svc, cfg):
        return None
    vision = Vision(svc, project, infos, analyses, cfg)
    found = [dp.public(s) for s in name_products(svc, project, vision, vision.detect(), cfg)]
    svc.projects.write_json(project.cache / "products.json", products_result(vision, found))
    if not found:
        log("Vision: no products found; using the regular plan.")
        return None
    log(f"Vision: {len(found)} products → one shot each")
    opening = None
    first = found[0]
    pacing = cfg["pacing"]
    opening_max = float(dp.settings(cfg.get("vision"))["opening_max"] or pacing["shot_max"])
    if first["start"] >= pacing["shot_min"] + 0.5:          # the package being opened, before the first product
        a, b = best_window(analyses[first["source"]], [0.0, first["start"] - 0.2],
                           min(opening_max, first["start"] - 0.2), "action")
        opening = (first["source"], a, b)
    return dp.product_plan(found, cfg, opening)
