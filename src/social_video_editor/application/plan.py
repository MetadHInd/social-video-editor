"""Use case: propose an editable plan (plan.json) from the analysis."""
from __future__ import annotations

from pathlib import Path

from ..domain import pauses
from ..domain.models import Project
from ..domain import photos as dp
from ..domain.photos import is_slideshow, photo_plan
from ..domain.selection import build_plan
from ..domain.transitions import transition_duration
from ..log import log
from .analyze import prepare_sources
from .explainer import frame_plan, script_plan
from .products import product_plan_or_none
from .services import Services


def make_plan(svc: Services, project: Project, force: bool = False) -> Path | None:
    if svc.projects.exists(project.plan_path) and not force:
        log(f"{project.plan_path} already exists (use --force to redo it; a copy is kept).")
        return None
    infos, analyses = prepare_sources(svc, project)
    if is_slideshow(project.cfg, infos):                      # photos: one shot per photo, chronological
        shown, skipped = skip_photos(svc, project, infos)
        plan = photo_plan(shown, project.cfg)
        if skipped:
            plan["skipped_photos"] = skipped
        log(f"Photo slideshow: {len(plan['shots'])} photos"
            + (f" ({len(skipped)} left out: near-duplicates / blurred, see skipped_photos)" if skipped else ""))
    elif project.cfg.get("talk"):                             # meetings / classes: all the speech, long pauses shortened
        plan = talk_plan(infos, analyses, project.cfg)
    elif project.cfg.get("explainer"):                        # a script said to the camera + its cues as cutaways
        plan = script_plan(svc, project, analyses, infos)    # infos: a source without a picture is a voice memo
    else:
        plan = product_plan_or_none(svc, project, infos, analyses, project.cfg)     # unboxing + vision model
    if not plan:
        plan = _video_plan(infos, analyses, project.cfg)
    if not is_slideshow(project.cfg, infos) and not project.cfg.get("talk"):
        cut = pauses.shorten_plan_pauses(plan, analyses, project.cfg)    # every shot with voice
        if cut:
            log(f"Speech: {cut} long pauses shortened inside the shots with voice (pauses.min_pause)")
    if project.cfg.get("explainer") and plan.get("sentences"):
        frame_plan(svc, project, plan, infos, analyses)
    path = svc.projects.save_plan(project, plan)
    log(f"Plan saved to {path}")
    return path


QUALITY_CACHE = "photo_quality.json"      # in the project cache: {"<path>|<size>|<mtime_ns>": measure}


def skip_photos(svc: Services, project: Project, infos: list[dict]) -> tuple[list[dict], list[dict]]:
    """photo.skip_duplicates / photo.skip_blurred (both off by default): (the photos to show, the ones
    left out with why). Each photo is measured once (its pixels, read by the video analyzer) and the
    measure is cached per file; a photo that cannot be read is kept."""
    s = dp.quality_settings(project.cfg)
    photos = [i for i in infos if i.get("image", True)]
    if not (s["skip_duplicates"] or s["skip_blurred"]) or not photos:
        return infos, []
    path = project.cache / QUALITY_CACHE
    cached = svc.projects.read_json(path)
    cached = cached if isinstance(cached, dict) else {}
    measures, fresh = {}, 0
    for info in photos:
        fp = svc.probe.fingerprint(info["path"])
        key = f"{fp['path']}|{fp['size']}|{fp['mtime_ns']}"
        m = cached.get(key)
        if not isinstance(m, dict) or m.get("v") != dp.MEASURE_VERSION:
            try:
                frames = svc.video.read_frames(str(info["path"]), [0.0])
                m = dp.measure_photo(frames[0]) if frames else {"v": dp.MEASURE_VERSION}
            except (RuntimeError, OSError, ValueError) as e:
                log(f"Photos: {Path(info['path']).name} could not be measured ({e}); it is kept")
                m = {"v": dp.MEASURE_VERSION}
            cached[key] = m
            fresh += 1
        measures[str(info["path"])] = m
    if fresh:
        svc.projects.write_json(path, cached)
    kept, skipped = dp.skip_photos(photos, measures, s)
    for x in skipped:
        log(f"Photos: left out {Path(x['source']).name} — {x['why']}")
    return kept, skipped


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


def talk_plan(infos: list[dict], analyses: list[dict], cfg: dict) -> dict:
    """Every video source whole and in order, its long pauses shortened (domain/pauses.py)."""
    shots = [{"source": k, "start": 0.0, "end": round(float(i["duration"]), 3), "speed": 1,
              "transition": "cut", "effects": []} for k, i in enumerate(infos) if not i.get("image")]
    plan = {"note": "", "shots": shots, "texts": []}
    before = sum(s["end"] for s in shots)
    cut = pauses.shorten_plan_pauses(plan, analyses, cfg, whole=True)
    p = pauses.settings(cfg)
    plan["note"] = (f"Talk: all the speech in order, {cut} pauses of {p['min_pause']} s or more "
                    f"shortened to {p['keep_pause']} s")
    log(f"Talk: {cut} long pauses shortened, {_mmss(before)} → {_mmss(_length(plan, cfg))}")
    return plan


def _length(plan: dict, cfg: dict) -> float:
    shots = plan["shots"]
    return sum(s["end"] - s["start"] for s in shots) - sum(
        transition_duration(s["transition"], cfg) for s in shots[1:] if s["transition"] != "cut")


def _mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"
