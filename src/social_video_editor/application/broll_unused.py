"""Use case: the third-party clips no plan uses any more (`sve broll <project> --unused [--yes]`).

`sve broll` keeps every clip it downloaded in footage/ (one that was turned down later, or replaced
with --force, stays there with `"used": false` in credits.json), and its search answers in the project
cache. This lists them with their size; only with `yes` are they deleted: the clip, its record in
credits.json, the frame the decider looked at, and the search cache. A clip that plan.json or
plan_previous.json names is never deleted, whatever its record says.
"""
from __future__ import annotations

from pathlib import Path

from ..domain import footage
from ..domain.models import Project
from ..log import log
from .broll import FRAMES, SEARCH_CACHE
from .cutaways import footage_records
from .services import Services

PLANS = ("plan.json", "plan_previous.json")


def unused_footage(svc: Services, project: Project, delete: bool = False) -> dict:
    """→ {"clips": [{"file", "bytes", "exists", "source", "query"}], "kept": [file…] (marked unused but a
    plan names them), "cache": [{"file", "bytes"}], "bytes", "deleted"}."""
    records = footage_records(svc, project)
    referenced: set[str] = set()
    for name in PLANS:
        referenced |= footage.plan_files(svc.projects.read_json(project.dir / name))
    unused, kept = footage.unused_clips([records[k] for k in sorted(records)], referenced)
    clips = [{"file": str(r["file"]), "bytes": svc.projects.file_size(project.dir / str(r["file"])),
              "exists": svc.projects.exists(project.dir / str(r["file"])), "source": r.get("source"),
              "query": r.get("query")} for r in unused]
    cache = [{"file": f"{project.cache.name}/{SEARCH_CACHE}", "bytes": svc.projects.file_size(project.cache / SEARCH_CACHE)}
             ] if svc.projects.exists(project.cache / SEARCH_CACHE) else []
    out = {"clips": clips, "kept": [str(r["file"]) for r in kept], "cache": cache,
           "bytes": sum(c["bytes"] for c in clips + cache), "deleted": False}
    if not delete or not (clips or cache):
        return out
    for c in clips:
        svc.projects.remove(project.dir / c["file"])
        svc.projects.remove(project.cache / FRAMES / f"{Path(c['file']).stem}.jpg")
    gone = {c["file"] for c in clips if not svc.projects.exists(project.dir / c["file"])}   # one kept open stays
    if gone:
        svc.projects.write_json(project.dir / footage.FOLDER / footage.RECORD,
                                [records[k] for k in sorted(records) if k not in gone])
    if cache:
        svc.projects.remove(project.cache / SEARCH_CACHE)
    log(f"B-roll: deleted {len(gone)} unused clips and {len(cache)} cache files ({footage.megabytes(out['bytes'])})")
    out["deleted"] = True
    return out


def unused_lines(result: dict, shown: int = 20) -> list[str]:
    """What `sve broll --unused` prints."""
    clips, cache = result["clips"], result["cache"]
    if not clips and not cache:
        lines = ["B-roll: every clip in footage/ is used by the plan; no search cache to clean."]
    else:
        head = "deleted" if result["deleted"] else "not used by any plan"
        lines = [f"B-roll {head}: {len(clips)} clips, {len(cache)} cache files · {footage.megabytes(result['bytes'])}"]
        for c in clips[:shown]:
            missing = "" if c["exists"] else " (file already gone: only its record)"
            query = f" · “{c['query']}”" if c.get("query") else ""
            lines.append(f"  {c['file']} · {footage.megabytes(c['bytes'])}{query}{missing}")
        if len(clips) > shown:
            lines.append(f"  … {len(clips) - shown} more")
        lines += [f"  {c['file']} · {footage.megabytes(c['bytes'])} (search answers, cached a day)" for c in cache]
        if not result["deleted"]:
            lines.append("Nothing was deleted. To delete them: sve broll <project> --unused --yes")
    if result["kept"]:
        lines.append(f"Kept: {len(result['kept'])} clips marked unused that a plan still names "
                     f"({', '.join(result['kept'][:3])}{' …' if len(result['kept']) > 3 else ''})")
    return lines
