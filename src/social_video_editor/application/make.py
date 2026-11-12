"""Use case: the whole video with one command (`sve make <project>`).

plan (when there is none, or with force) → footage: fetched, or drawn by the engine when no clip is
good enough (application/broll.py) → render (a draft, or final) → automatic review (`sve check`) →
a short summary of what was made and what is left for a person. It can be run again: a plan that
exists is kept, clips already fetched and segments already rendered are reused.
"""
from __future__ import annotations

from ..domain.models import Project
from ..log import log
from .broll import broll_lines, fetch_broll
from .plan import make_plan
from .render import render
from .services import Services
from .view import load_plan


def make(svc: Services, project: Project, final: bool = False, force: bool = False) -> dict:
    """→ {"plan": bool (made now), "broll": fetch_broll result, "video": path, "check": check.json content}."""
    from .check import check_version

    made = False
    if force or not svc.projects.exists(project.plan_path):
        log("Make: plan")
        make_plan(svc, project, force=True)
        made = True
    log("Make: footage (fetched, or drawn when no clip is good enough)")
    footage = fetch_broll(svc, project)
    log("Make: render" + ("" if final else " (draft)"))
    video = render(svc, project, load_plan(svc, project), draft=not final)
    log("Make: review")
    checked, _ = check_version(svc, str(project.dir), video.parent.name)
    return {"plan": made, "broll": footage, "video": video, "check": checked}


def make_lines(result: dict) -> list[str]:
    """What `sve make` prints: the footage summary, the video and what the review found."""
    c = result["check"]
    lines = [*broll_lines(result["broll"]),
             f"Video: {result['video']}",
             f"Review {c['version']}: {'OK' if c['ok'] else 'PROBLEMS'} — {len(c['errors'])} errors, "
             f"{len(c['warnings'])} warnings"]
    lines += [f"  ERROR  {e}" for e in c["errors"]]
    lines += [f"  warn   {w}" for w in c["warnings"][:6]]
    if len(c["warnings"]) > 6:
        lines.append(f"  … +{len(c['warnings']) - 6} more (check.json)")
    return lines
