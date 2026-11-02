"""Use case: propose an editable plan (plan.json) from the analysis."""
from __future__ import annotations

from pathlib import Path

from ..domain.models import Project
from ..domain.selection import build_plan
from ..log import log
from .analyze import prepare_sources
from .products import product_plan_or_none
from .services import Services


def make_plan(svc: Services, project: Project, force: bool = False) -> Path | None:
    if project.plan_path.exists() and not force:
        log(f"{project.plan_path} already exists (use --force to redo it; a copy is kept).")
        return None
    infos, analyses = prepare_sources(svc, project)
    plan = product_plan_or_none(svc, project, infos, analyses, project.cfg)     # unboxing + vision model
    path = svc.projects.save_plan(project, plan or build_plan(analyses, project.cfg, report=log))
    log(f"Plan saved to {path}")
    return path
