"""Use case: propose an editable plan (plan.json) from the analysis."""
from __future__ import annotations

from pathlib import Path

from ..domain.models import Project
from ..domain.photos import is_slideshow, photo_plan
from ..domain.selection import build_plan
from ..log import log
from .analyze import prepare_sources
from .products import product_plan_or_none
from .services import Services


def make_plan(svc: Services, project: Project, force: bool = False) -> Path | None:
    if svc.projects.exists(project.plan_path) and not force:
        log(f"{project.plan_path} already exists (use --force to redo it; a copy is kept).")
        return None
    infos, analyses = prepare_sources(svc, project)
    if is_slideshow(project.cfg, infos):                      # photos: one shot per photo, chronological
        plan = photo_plan(infos, project.cfg)
        log(f"Photo slideshow: {len(plan['shots'])} photos")
    else:
        plan = product_plan_or_none(svc, project, infos, analyses, project.cfg)     # unboxing + vision model
    path = svc.projects.save_plan(project, plan or _video_plan(infos, analyses, project.cfg))
    log(f"Plan saved to {path}")
    return path


def _video_plan(infos: list[dict], analyses: list[dict], cfg: dict) -> dict:
    """Regular plan from the video sources (photo sources, if any, are left out: `slideshow: true`
    makes a photo plan instead). Source indices stay those of the full source list."""
    videos = [k for k, i in enumerate(infos) if not i.get("image")]
    if len(videos) == len(infos):
        return build_plan(analyses, cfg, report=log)
    log(f"{len(infos) - len(videos)} photo sources not used by the video plan (set slideshow: true for photos)")
    plan = build_plan([analyses[k] for k in videos], cfg, report=log)
    for s in plan.get("shots", []) + plan.get("extra_candidates", []):
        if isinstance(s.get("source"), int):
            s["source"] = videos[s["source"]]
    return plan
