"""Use case: the whole video with one command (`sve make <project>`), reviewing and fixing itself.

plan (when there is none, or with force) → footage: fetched, or drawn by the engine when no clip is
good enough (application/broll.py) → the engine reviews the plan as placed on the video and applies
its fixed remedies (domain/review.py), fetching footage again for what it added, until nothing
changes → render (a draft, or final) → automatic review (`sve check`) → remedies for what the check
found on screen → render again, at most `review.rounds` renders → what is left for a person.

It can be run again: a plan that exists is kept, clips already fetched and segments already
rendered are reused. What was fixed and the decisions left are written to <project>/review.json
(`sve report` shows the decisions).
"""
from __future__ import annotations

from ..domain import review
from ..domain import pip as pip_window
from ..domain.cutaways import READ_SECONDS, resolve, sentence_spans
from ..domain.models import Project
from ..domain.timeline import compute_timeline
from ..log import log
from .analyze import prepare_sources
from .broll import broll_lines, fetch_broll
from .cutaways import entrance_of
from .explainer import frame_plan
from .plan import make_plan
from .render import render
from .services import Services
from .view import load_plan

PLAN_PASSES = 4             # reviews of the plan before a render (each one cheap: nothing is rendered)


def layout(svc: Services, project: Project, plan: dict) -> review.Layout:
    """The plan's cutaways placed on the video, without rendering anything."""
    cfg = project.cfg
    tl = compute_timeline(plan, cfg)
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    entrance = entrance_of(svc, cfg, W, H)
    cutaways = plan.get("cutaways") or []
    placed, _ = resolve(plan, tl, int(cfg["output"]["fps"]), entrance, pip_window.settings(cfg)) if cutaways else ([], [])
    minimum = {i: float(entrance(c)) + READ_SECONDS for i, c in enumerate(cutaways)}
    return review.Layout(placed, minimum, sentence_spans(plan, tl), tl.total)


def make(svc: Services, project: Project, final: bool = False, force: bool = False) -> dict:
    """→ {"plan": bool (made now), "broll": fetch_broll result of the first pass, "video": path,
    "check": check.json content, "fixed": [what the engine changed], "pending": [decisions],
    "renders": int}."""
    from .check import check_version

    made = False
    if force or not svc.projects.exists(project.plan_path):
        log("Make: plan")
        make_plan(svc, project, force=True)
        made = True
    explainer = bool(project.cfg.get("explainer"))
    rounds = max(int(review.settings(project.cfg)["rounds"]), 1)
    tried: set[str] = set()
    fixed: list[str] = []
    footage = None
    pending: list[review.Issue] = []
    video, checked, renders = None, None, 0
    while True:
        if explainer:
            plan_pending, got = _settle_plan(svc, project, tried, fixed)
        else:
            log("Make: footage (fetched, or drawn when no clip is good enough)")
            plan_pending, got = [], fetch_broll(svc, project)
        footage = footage or got
        log("Make: render" + ("" if final else " (draft)") + (f", round {renders + 1}" if renders else ""))
        plan = load_plan(svc, project)
        video = render(svc, project, plan, draft=not final)
        renders += 1
        log("Make: review")
        checked, _ = check_version(svc, str(project.dir), video.parent.name)
        if not explainer:
            break
        seen = review.review_render(plan, layout(svc, project, plan), checked)
        if renders >= rounds:                       # no render left to try a remedy: all of it is for a person
            pending = plan_pending + seen
            break
        done, render_pending = review.apply(plan, seen, tried)
        pending = plan_pending + render_pending
        if not done:
            break
        fixed += done
        for line in done:
            log(f"  fixed: {line}")
        _reframe(svc, project, plan)
        svc.projects.save_plan(project, plan)
    result = {"plan": made, "broll": footage, "video": video, "check": checked, "fixed": fixed,
              "pending": [x.as_dict() for x in pending], "renders": renders}
    if explainer:
        svc.projects.write_json(project.dir / review.REVIEW_FILE, {
            "version": checked["version"] if checked else None, "renders": renders, "fixed": fixed,
            "pending": result["pending"]})
    return result


def _settle_plan(svc: Services, project: Project, tried: set[str], fixed: list[str]) -> tuple[list, dict]:
    """Footage, then the plan's review and remedies until nothing changes → (findings left, first
    footage result)."""
    first = None
    pending: list[review.Issue] = []
    for _ in range(PLAN_PASSES):
        log("Make: footage (fetched, or drawn when no clip is good enough)")
        got = fetch_broll(svc, project)
        first = first or got
        plan = load_plan(svc, project)
        found = review.review_plan(plan, layout(svc, project, plan), project.cfg)
        done, pending = review.apply(plan, found, tried)
        if not done:
            break
        fixed += done
        for line in done:
            log(f"  fixed: {line}")
        _notes_again(project, plan)
        _reframe(svc, project, plan)
        svc.projects.save_plan(project, plan)
    return pending, first


def _notes_again(project: Project, plan: dict) -> None:
    """The notes around the presenter follow what is on screen: proposed again after the cutaways changed."""
    from ..domain import support
    from ..domain.explainer import script_settings

    if not script_settings(project.cfg).get("support"):
        return
    kept = [c for c in plan.get("cutaways") or [] if c.get("_auto") != "support"]
    notes = support.propose({**plan, "cutaways": kept}, project.cfg.get("language", "es"))
    plan["cutaways"] = sorted(kept + notes, key=lambda c: c.get("at_sentence", 0))


def _reframe(svc: Services, project: Project, plan: dict) -> None:
    """The punch-ins follow the cutaways (a cut under one is not seen): framed again after a change."""
    if plan.get("sentences"):
        infos, analyses = prepare_sources(svc, project)
        frame_plan(svc, project, plan, infos, analyses)


def make_lines(result: dict) -> list[str]:
    """What `sve make` prints: the footage summary, the video, what the engine fixed and what the
    review left for a person."""
    c = result["check"]
    lines = [*broll_lines(result["broll"]),
             f"Video: {result['video']}",
             f"Review {c['version']}: {'OK' if c['ok'] else 'PROBLEMS'} — {len(c['errors'])} errors, "
             f"{len(c['warnings'])} warnings"]
    lines += [f"  ERROR  {e}" for e in c["errors"]]
    lines += [f"  warn   {w}" for w in c["warnings"][:6]]
    if len(c["warnings"]) > 6:
        lines.append(f"  … +{len(c['warnings']) - 6} more (check.json)")
    if result.get("fixed"):
        lines.append(f"Fixed by the engine ({len(result['fixed'])}, {result.get('renders', 1)} render(s)):")
        lines += [f"  ✓ {x}" for x in result["fixed"]]
    lines += review.decision_lines(result.get("pending") or [])
    return lines
