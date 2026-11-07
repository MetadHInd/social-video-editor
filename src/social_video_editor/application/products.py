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

import numpy as np

from ..domain import products as dp
from ..domain.models import Project, UserError
from ..domain.timebase import mmss
from ..log import log
from .analyze import prepare_sources
from .services import Services

LABEL_TEMPLATES = {"es": "una foto de {}", "en": "a photo of {}"}


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

    def detect(self) -> list[dict]:
        sources = [self.source(i) for i in range(len(self.infos))]
        pos, neg = self.prompts()
        vocab, labels = self.vocabulary()
        packaging = self.texts(self.v["packaging"]) if self.v.get("unpacked") and self.v.get("packaging") else None
        return dp.detect_products(sources, pos, neg, vocab, labels, self.v, packaging)

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
    return {"segments": segments, "model": vision.svc.visual.model_name(vision.v), "language": vision.language}


def find_products(svc: Services, project: Project) -> dict:
    """Detects the products of every source, writes cache/products.json and returns its content."""
    cfg = project.cfg
    infos, analyses = prepare_sources(svc, project, cfg)
    vision_available(svc, cfg, explicit=True)
    vision = Vision(svc, project, infos, analyses, cfg)
    result = products_result(vision, vision.detect())
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
    found = vision.detect()
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
