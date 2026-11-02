"""Use cases around music: analysis (cached), start offset + beat alignment, download."""
from __future__ import annotations

from pathlib import Path

from ..domain.models import Project
from ..domain.music import auto_start, beats_in_video
from ..domain.timeline import align_to_beats, compute_timeline, snap_cuts_to_beats
from ..log import log
from .services import Services


def music_analysis(svc: Services, path: Path, cache_dir: Path) -> dict:
    key = path.stem.replace(" ", "_")
    am = svc.cache.load(cache_dir, "music", key, newer_than=path)
    if am is None:
        am = svc.audio.music(path)
        svc.cache.save(cache_dir, "music", key, am)
    return am


def prepare_music(svc: Services, project: Project, plan: dict, cfg: dict, infos: list[dict],
                  no_music: bool) -> Path | None:
    """Picks the track, computes where it starts and aligns the cuts to its beats.

    Stores the start (seconds of the song) in cfg["music"]["_start"]. Returns the track to mix
    (None with no_music: same beat-aligned cuts, without the song).
    """
    music = svc.projects.find_music(cfg["music"], project.dir)
    if not music:
        log("No music (put a track in music/ or set settings.music.file)")
        return None
    m = cfg["music"]
    am = music_analysis(svc, music, project.cache)
    T = am.get("interval", 0.5)
    if m.get("start_at", 0) == "auto":
        drop_at = m.get("drop_at")
        if drop_at is None:
            first = plan["shots"][0]
            drop_at = int(first["beats"]) * T if "beats" in first else compute_timeline(plan, cfg)[0].dur
        start = auto_start(am, float(drop_at))
    else:
        start = float(m.get("start_at", 0))
    m["_start"] = round(start, 3)
    beats = beats_in_video(am, start, 600)
    durations = [i["duration"] for i in infos]
    if any("beats" in s for s in plan["shots"]) or m.get("align") == "exact":
        align_to_beats(plan, beats, cfg, durations)
        mode = "every cut on a beat"
    elif m.get("cuts_on_beat", True):
        mode = f"{snap_cuts_to_beats(plan, beats, cfg, durations)} cuts moved to the beat"
    else:
        mode = "no cut adjustment"
    log(f"Music: {music.name} — {am.get('tempo')} BPM, starts at {start:.2f} s "
        f"(song drop at {am.get('drop', 0):.2f} s) — {mode}"
        + (" — NOT mixed (--no-music)" if no_music else ""))
    return None if no_music else music


def download_music(svc: Services, query: str) -> tuple[Path, dict]:
    """Downloads a song (first YouTube result or URL) into the workspace music folder and analyzes it."""
    dest = svc.projects.music_dir()
    path = svc.downloader.download(query, dest)
    return path, music_analysis(svc, path, dest)
