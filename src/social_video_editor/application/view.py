"""Use case: summary of the plan and the timeline (with the same beat alignment as the render)."""
from __future__ import annotations

import copy

from ..domain.models import Project, UserError
from ..domain.pieces import Group
from ..domain.timebase import mmss
from ..domain.timeline import absolute_texts
from .render import prepare_edit
from .services import Services


def load_plan(svc: Services, project: Project) -> dict:
    plan = svc.projects.load_plan(project)
    if plan is None:
        raise UserError("There is no plan.json; run first: sve plan <project>")
    return plan


def view_lines(svc: Services, project: Project) -> list[str]:
    plan, _, _, _, tl = prepare_edit(svc, project, load_plan(svc, project), copy.deepcopy(project.cfg))
    lines = [f"{project.name} — recipe '{project.recipe}' — {len(tl)} shots — {tl.total:.1f} s"]

    def shot_line(clip) -> str:
        fx = ",".join(e["type"] for e in clip.data.get("effects", []))
        return (f" {clip.index:>2} [{mmss(clip.out_start)}–{mmss(clip.out_end)}] {clip.piece.describe()} "
                f"{clip.piece.transition:<12} {clip.piece.role:<10} {fx}").rstrip()

    fps = int(project.cfg["output"]["fps"])
    for item in tl.sequence.children():
        if isinstance(item, Group):
            shots = item.shots()
            lines.append(f"    group '{item.label}': {len(shots)} shots, {item.duration(fps):.2f} s")
        for shot in item.shots():
            lines.append(shot_line(tl[shot.index]))
    for tx in absolute_texts(plan, tl):
        lines.append(f"   text [{mmss(tx['start'])}–{mmss(tx['end'])}] ({tx.get('style', 'simple')}) {tx['text']}")
    return lines
