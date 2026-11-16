"""Use case: the whole video with one command (`sve make <project>`), reviewing and fixing itself.

plan (when there is none, or with force) → footage: fetched, or drawn by the engine when no clip is
good enough (application/broll.py) → the engine reviews the plan as placed on the video and applies
its fixed remedies (domain/review.py), fetching footage again for what it changed, until nothing
changes → render (a draft, or final) → automatic review (`sve check`) → remedies for what the check
found on screen or in the sound → render again, at most `review.rounds` renders → what is left for a
person. Every recipe goes through it: an explainer gets the whole review of its script and cutaways;
the others the findings that apply to them (texts too short to read or outside the safe zone, a face
cut by the frame, music covering the voice, a clip that stops moving…). `sve plan` and `sve render`
never apply any of it: only `sve make` changes a plan that exists.

Each review round renders a new attempt; the attempt a later round replaces is deleted once the later
one is rendered (`review.keep_attempts: true` keeps them), so one run leaves one version folder. A
folder that existed before the run is never touched.

Remedies that are settings (the music level under the voice) are not written to project.yaml: they
are kept in <project>/review.json ("settings"), used by every later `sve make` and shown by `sve report`.

It can be run again: a plan that exists is kept, clips already fetched and segments already
rendered are reused. What was fixed and the decisions left are written to <project>/review.json
(`sve report` shows the decisions).
"""
from __future__ import annotations

import copy
import dataclasses

from ..domain import review
from ..domain import pip as pip_window
from ..domain.cutaways import READ_SECONDS, resolve, sentence_spans
from ..domain.models import Project
from ..domain.timeline import absolute_texts, compute_timeline
from ..log import log
from .analyze import prepare_sources
from .broll import broll_lines, fetch_broll
from .cutaways import entrance_of
from .decide import run_decided
from .explainer import frame_plan
from .plan import make_plan
from .render import render, render_shorts
from .services import Services
from .view import load_plan

PLAN_PASSES = 4             # reviews of the plan before a render (each one cheap: nothing is rendered)


def layout(svc: Services, project: Project, plan: dict) -> review.Layout:
    """The plan's cutaways and texts placed on the video, without rendering anything."""
    cfg = project.cfg
    tl = compute_timeline(plan, cfg)
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    entrance = entrance_of(svc, cfg, W, H, base=project.dir)
    cutaways = plan.get("cutaways") or []
    placed, _ = resolve(plan, tl, int(cfg["output"]["fps"]), entrance, pip_window.settings(cfg)) if cutaways else ([], [])
    minimum = {i: float(entrance(c)) + READ_SECONDS for i, c in enumerate(cutaways)}
    indexed = {**plan, "texts": [dict(t, _i=k) for k, t in enumerate(plan.get("texts") or []) if isinstance(t, dict)]}
    texts = [(int(t["_i"]), float(t["start"]), float(t["end"])) for t in absolute_texts(indexed, tl)]
    return review.Layout(placed, minimum, sentence_spans(plan, tl), tl.total, texts)


def with_settings(project: Project, adjust: dict) -> Project:
    """The project with the settings `sve make` adjusted (review.json "settings") over its own."""
    if not adjust:
        return project
    cfg = copy.deepcopy(project.cfg)
    for section, values in adjust.items():
        if isinstance(values, dict) and isinstance(cfg.get(section), dict):
            cfg[section].update(values)
        else:
            cfg[section] = copy.deepcopy(values)
    return dataclasses.replace(project, cfg=cfg)


class _Sources:
    """The media infos and analyses of the sources, read once per run (only when a face is looked for)."""

    def __init__(self, svc: Services, project: Project):
        self.svc, self.project, self.got = svc, project, None

    def get(self) -> tuple[list[dict], list[dict]]:
        if self.got is None:
            self.got = prepare_sources(self.svc, self.project)
        return self.got


def make(svc: Services, project: Project, final: bool = False, force: bool = False,
         shorts: list[str] | None = None, all_shorts: bool = False) -> dict:
    """→ {"plan": bool (made now), "broll": fetch_broll result of the first pass, "video": path,
    "check": check.json content, "fixed": [what the engine changed], "pending": [decisions],
    "renders": int, "settings": {adjusted settings}, "shorts": [{"name", "video", "check"}]}.
    shorts / all_shorts: also render those shorts of the plan (or all of them) once the video is settled."""
    from .check import check_version

    made = False
    if force or not svc.projects.exists(project.plan_path):
        log("Make: plan")
        make_plan(svc, project, force=True)
        made = True
    explainer = bool(project.cfg.get("explainer"))
    r = review.settings(project.cfg)
    rounds = max(int(r["rounds"]), 1)
    saved = svc.projects.read_json(project.dir / review.REVIEW_FILE)
    adjust: dict = copy.deepcopy((saved or {}).get("settings") or {}) if isinstance(saved, dict) else {}
    work = with_settings(project, adjust)
    sources = _Sources(svc, project)
    tried: set[str] = set()
    fixed: list[str] = []
    footage = None
    pending: list[review.Issue] = []
    video, checked, renders, attempt = None, None, 0, None
    while True:
        plan_pending, got = _settle_plan(svc, work, tried, fixed, adjust, explainer, sources)
        work = with_settings(project, adjust)
        footage = footage or got
        log("Make: render" + ("" if final else " (draft)") + (f", round {renders + 1}" if renders else ""))
        plan = load_plan(svc, work)
        video = render(svc, work, plan, draft=not final)
        renders += 1
        if attempt is not None and attempt != video.parent and not r["keep_attempts"]:
            log(f"Make: {attempt.name} replaced by {video.parent.name} (an attempt of this run: removed)")
            svc.projects.remove(attempt)
        attempt = video.parent
        log("Make: review")
        checked, _ = check_version(svc, str(project.dir), video.parent.name)
        seen = run_decided(svc, work, lambda d: review.review_render(plan, layout(svc, work, plan), checked, work.cfg, d))
        if renders >= rounds:                       # no render left to try a remedy: all of it is for a person
            pending = plan_pending + seen
            break
        done, render_pending = review.apply(plan, seen, tried, adjust)
        pending = plan_pending + render_pending
        if not done:
            break
        fixed += done
        for line in done:
            log(f"  fixed: {line}")
        work = with_settings(project, adjust)
        _reframe(svc, work, plan, sources)
        svc.projects.save_plan(work, plan)
    made_shorts = []
    if shorts or all_shorts:
        plan = load_plan(svc, work)
        for path in render_shorts(svc, work, plan, None if all_shorts else shorts, draft=not final):
            short_check, _ = check_version(svc, str(project.dir), path.parent.name)
            made_shorts.append({"name": path.parent.name, "video": path, "check": short_check})
    result = {"plan": made, "broll": footage, "video": video, "check": checked, "fixed": fixed,
              "pending": [x.as_dict() for x in pending], "renders": renders, "settings": adjust,
              "shorts": made_shorts}
    svc.projects.write_json(project.dir / review.REVIEW_FILE, {
        "version": checked["version"] if checked else None, "renders": renders, "fixed": fixed,
        "pending": result["pending"], **({"settings": adjust} if adjust else {})})
    return result


def _settle_plan(svc: Services, project: Project, tried: set[str], fixed: list[str], adjust: dict,
                 explainer: bool, sources: _Sources) -> tuple[list, dict]:
    """Footage, then the plan's review and remedies until nothing changes → (findings left, first
    footage result)."""
    first = None
    pending: list[review.Issue] = []
    face = (project.cfg.get("framing") or {}).get("face")
    faces = review.settings(project.cfg)["faces"] and face not in (None, False, "false", "off", "no")
    for _ in range(PLAN_PASSES):
        log("Make: footage (fetched, or drawn when no clip is good enough)")
        got = fetch_broll(svc, project)
        first = first or got
        plan = load_plan(svc, project)
        placed = layout(svc, project, plan)
        found = run_decided(svc, project, lambda d: review.review_plan(plan, placed, project.cfg, decide=d))
        if faces and plan.get("shots"):
            infos, analyses = sources.get()
            found += review.faces_cut(plan, infos, analyses, project.cfg)
        done, pending = review.apply(plan, found, tried, adjust)
        if not done:
            break
        fixed += done
        for line in done:
            log(f"  fixed: {line}")
        if explainer:
            _notes_again(svc, project, plan)
        _reframe(svc, project, plan, sources)
        svc.projects.save_plan(project, plan)
    return pending, first


def _notes_again(svc: Services, project: Project, plan: dict) -> None:
    """The notes around the presenter follow what is on screen: proposed again after the cutaways changed."""
    from ..domain import support
    from ..domain.explainer import script_settings

    if not script_settings(project.cfg).get("support"):
        return
    kept = [c for c in plan.get("cutaways") or [] if c.get("_auto") != "support"]
    language = project.cfg.get("language", "es")
    notes = run_decided(svc, project, lambda d: support.propose({**plan, "cutaways": kept}, language, decide=d,
                                                                settings=project.cfg))
    plan["cutaways"] = sorted(kept + notes, key=lambda c: c.get("at_sentence", 0))


def _reframe(svc: Services, project: Project, plan: dict, sources: _Sources | None = None) -> None:
    """The punch-ins follow the cutaways (a cut under one is not seen): framed again after a change. A
    shot the review set to plain framing (face_out_of_frame) stays plain."""
    if not plan.get("sentences"):
        return
    infos, analyses = sources.get() if sources is not None else prepare_sources(svc, project)
    frame_plan(svc, project, plan, infos, analyses)
    for shot in plan.get("shots") or []:
        if shot.get("_plain"):
            shot.pop("zoom", None)
            shot["effects"] = [e for e in shot.get("effects") or [] if e.get("type") not in ("push_in", "pull_out")]


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
    if result.get("settings"):
        lines.append("Settings this sve make renders with (review.json; add them to the project to keep them in "
                     "sve render): " + ", ".join(f"{s}.{k}={v!r}" for s, vals in result["settings"].items()
                                                 for k, v in vals.items()))
    for s in result.get("shorts") or []:
        sc = s["check"]
        lines.append(f"Short {s['name']}: {s['video']} — {'OK' if sc['ok'] else 'PROBLEMS'} "
                     f"({len(sc['errors'])} errors, {len(sc['warnings'])} warnings)")
    lines += review.decision_lines(result.get("pending") or [])
    return lines
