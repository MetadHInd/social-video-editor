"""Use case: the explainer plan — a script (script.md) and the presenter's recording become the
main track and its cutaways (domain/script.py, script_align.py, explainer.py, cutaways.py)."""
from __future__ import annotations

from collections import Counter

from ..domain import director, explainer, script_align, support
from ..domain.graphics import TEMPLATES
from ..domain.models import PlanError, Project
from ..domain.script import GRAPHIC, parse_script
from ..domain.timebase import mmss
from ..domain.timeline import compute_timeline
from ..log import log
from .cutaways import place_cutaways
from .graphic import build_scene, graphics_theme
from .services import Services

MAX_LOG_LINES = 12


def script_plan(svc: Services, project: Project, analyses: list[dict]) -> dict:
    """plan.json from the script: every sentence found in the recording (the chosen take), joined into
    shots in script order, and one cutaway per cue. Pauses are shortened afterwards (application/plan.py)."""
    cfg = project.cfg
    name, text = svc.projects.read_script(project)
    theme = graphics_theme(svc, cfg)                     # also registers the pack's own templates
    script = parse_script(text, svc.projects.translate_cue, set(TEMPLATES))
    _check_graphics(svc, cfg, script, theme)
    words = script_align.transcript(analyses)
    if not words:
        raise PlanError("The recording has no transcribed speech: the script cannot be matched to it "
                        "(is there a voice in the sources? check transcription.language)")
    matches = script_align.align([s.text for s in script.sentences], words, cfg)
    plan = explainer.build_plan(script, matches, cfg, name)
    cues = len(plan["cutaways"])
    overlay = {name for name, t in TEMPLATES.items() if getattr(t, "overlay", False)}
    if director.settings(cfg)["auto_broll"]:                # footage where the presenter would stay alone too long
        cuts = director.propose(plan, cfg, overlay)
        plan["cutaways"] = sorted(plan["cutaways"] + cuts, key=lambda c: c.get("at_sentence", 0))
        plan["note"] += f", {len(cuts)} b-roll proposed (sve broll fetches them)"
    if explainer.script_settings(cfg).get("support"):       # notes around the presenter where nothing else is on screen
        notes = support.propose(plan, cfg.get("language", "es"))
        plan["cutaways"] = sorted(plan["cutaways"] + notes, key=lambda c: c.get("at_sentence", 0))
        plan["note"] += f", {len(notes)} proposed overlays"
    auto = Counter(c.get("_auto") for c in plan["cutaways"] if c.get("_auto"))
    log(f"Script {name}: {len(script.sentences)} sentences, {sum(m is not None for m in matches)} found, {cues} cues"
        + (f", {auto['director']} b-roll proposed" if auto.get("director") else "")
        + (f", {auto['support']} overlays proposed" if auto.get("support") else ""))
    lines = []
    for s, m in zip(script.sentences, matches):
        if m is None:
            lines.append(f"  ⚠ sentence {s.index} (line {s.line}) not found in the recording: {s.text}")
        elif m["takes"] > 1:
            lines.append(f"  sentence {s.index}: {m['takes']} takes, using the one at {mmss(m['start'])} "
                         f"(match {m['score']:.0%})")
        elif m["score"] < 0.8:
            lines.append(f"  sentence {s.index}: match {m['score']:.0%} at {mmss(m['start'])}: {s.text}")
    for line in lines[:MAX_LOG_LINES]:
        log(line)
    if len(lines) > MAX_LOG_LINES:
        log(f"  … +{len(lines) - MAX_LOG_LINES} more (sve report)")
    return plan


def _check_graphics(svc: Services, cfg: dict, script, theme: dict) -> None:
    """Builds every graphic cue once, so a bad cue is reported now with its script line (not at render)."""
    if svc.graphics is None or svc.graphics.ready():
        return
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    for cue in script.cues:
        if cue.kind != GRAPHIC:
            continue
        try:
            build_scene(svc, cfg, cue.spec, W, H, theme)
        except PlanError as e:
            raise PlanError(f"Script line {cue.line}: {e}") from None


def frame_plan(svc: Services, project: Project, plan: dict, infos: list[dict], analyses: list[dict]) -> None:
    """After the pauses are shortened: punch-ins at the visible jump cuts and framing on the face."""
    cfg = project.cfg
    tl = compute_timeline(plan, cfg)
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    placed, warnings = place_cutaways(svc, cfg, plan, tl, W, H)
    counts = explainer.frame_shots(plan, tl, placed, cfg, infos, analyses)
    windows = sum(c.pip for c in placed)
    log(f"Explainer: {len(tl)} shots, {tl.total:.1f} s, {len(placed)} cutaways"
        + (f" ({windows} with the presenter's window)" if windows else "") + f", {counts['punch_ins']} punch-ins"
        + (f", {counts['framed']} shots framed on the face" if counts["framed"] else ""))
    for w in warnings:
        log(f"  ⚠ {w}")
