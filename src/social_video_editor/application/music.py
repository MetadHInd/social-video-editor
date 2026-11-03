"""Use cases around music: analysis (cached), start offset + beat alignment, playlists (AutoMix),
photo durations fitted to the music, download."""
from __future__ import annotations

from pathlib import Path

from ..domain import automix
from ..domain.models import Project
from ..domain.music import ANALYSIS_VERSION, auto_start, beats_in_video, retime, tempo_lines
from ..domain.slideshow_timing import fit_plan_to_music
from ..domain.timeline import align_to_beats, compute_timeline, snap_cuts_to_beats
from ..log import log
from .services import Services


def music_analysis(svc: Services, path: Path, cache_dir: Path, save: bool = True) -> dict:
    """Cached analysis of a song (recomputed if the cache is older than the tempo engine).
    save=False never writes the cache (read-only uses such as `sve check`)."""
    key = path.stem.replace(" ", "_")
    am = svc.cache.load(cache_dir, "music", key, newer_than=path)
    if am is None or am.get("v") != ANALYSIS_VERSION:
        am = svc.audio.music(path)
        if save:
            svc.cache.save(cache_dir, "music", key, am)
    return am


def effective_analysis(am: dict, music_cfg: dict) -> dict:
    """The analysis with the recipe overrides music.bpm / music.tempo_factor applied."""
    return retime(am, music_cfg.get("bpm"), music_cfg.get("tempo_factor"))


def prepare_music(svc: Services, project: Project, plan: dict, cfg: dict, infos: list[dict],
                  no_music: bool) -> Path | None:
    """Picks the track, computes where it starts and aligns the cuts to its beats.

    Stores the start (seconds of the song) in cfg["music"]["_start"]. Returns the track to mix
    (None with no_music: same beat-aligned cuts, without the song).
    """
    if cfg["music"].get("playlist") and cfg["music"].get("file") not in (False, "none"):
        return prepare_playlist(svc, project, plan, cfg, infos, no_music)
    music = svc.projects.find_music(cfg["music"], project.dir)
    if not music:
        log("No music (put a track in music/ or set settings.music.file)")
        return None
    m = cfg["music"]
    am = effective_analysis(music_analysis(svc, music, project.cache), m)
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
    beats = beats_in_video(am, start, float(am.get("duration") or 0))     # the whole song (no cap)
    durations = [i["duration"] for i in infos]
    fitted = fit_plan_to_music(plan, automix.single_timeline(am, start, music.name), cfg, durations)
    if fitted and "skipped" not in fitted:
        mode = _fitted_line(fitted)
    elif any("beats" in s for s in plan["shots"]) or m.get("align") == "exact":
        align_to_beats(plan, beats, cfg, durations)
        mode = "every cut on a beat"
    elif m.get("cuts_on_beat", True):
        mode = f"{snap_cuts_to_beats(plan, beats, cfg, durations)} cuts moved to the beat"
    else:
        mode = "no cut adjustment"
    if fitted and "skipped" in fitted:
        log(f"Note: {fitted['skipped']}")
    log(f"Music: {music.name} — {am.get('tempo')} BPM, starts at {start:.2f} s "
        f"(song drop at {am.get('drop', 0):.2f} s) — {mode}"
        + (" — NOT mixed (--no-music)" if no_music else ""))
    for line in tempo_lines(am):
        log(line)
    return None if no_music else music


def _fitted_line(fitted: dict) -> str:
    return (f"{fitted['photos']} photos fitted to the music ({fitted['min']:.1f}–{fitted['max']:.1f} s each, "
            f"{fitted['length']:.1f} s)")


def prepare_playlist(svc: Services, project: Project, plan: dict, cfg: dict, infos: list[dict],
                     no_music: bool) -> Path | None:
    """music.playlist: every song analyzed, planned (domain.automix) and rendered as ONE continuous
    mix (cached WAV); the cuts are aligned to its merged beat grid (or the photos fitted to it).

    Stores in cfg["music"]: "_start" (second of the mix to start from: start_at when numeric, else 0)
    and "_automix" (songs, splices in video seconds, mix length) for the summary and `sve check`;
    cfg["music"]["_automix_timeline"] is the full mix timeline (beats included), popped by the render
    to write vN/automix.json.
    """
    m = cfg["music"]
    paths = svc.projects.find_playlist(m, project.dir)
    settings = automix.settings(m)
    work = project.cache / "automix"
    songs = []
    for p in paths:
        am = music_analysis(svc, p, project.cache)
        songs.append(automix.song(p.name, am, svc.encoder.music_features(p, am, work), settings))
    mix_plan = automix.plan_mix(songs, settings)
    mix = svc.encoder.render_automix(paths, mix_plan, work)
    tl = automix.mix_timeline(mix_plan, songs)
    start = m.get("start_at", 0)
    start = float(start) if isinstance(start, (int, float)) and not isinstance(start, bool) else 0.0
    m["_start"] = round(start, 3)
    shifted = {**tl, "duration": round(tl["duration"] - start, 4),
               "boundaries": [round(b - start, 4) for b in tl["boundaries"] if b > start],
               "beats": [round(b - start, 4) for b in tl["beats"] if b >= start],
               "downbeats": [round(b - start, 4) for b in tl["downbeats"] if b >= start]}
    durations = [i["duration"] for i in infos]
    fitted = fit_plan_to_music(plan, shifted, cfg, durations)
    if fitted and "skipped" not in fitted:
        mode = _fitted_line(fitted)
    elif any("beats" in s for s in plan["shots"]) or m.get("align") == "exact":
        align_to_beats(plan, shifted["beats"], cfg, durations)
        mode = "every cut on a beat"
    elif m.get("cuts_on_beat", True):
        mode = f"{snap_cuts_to_beats(plan, shifted['beats'], cfg, durations)} cuts moved to the beat"
    else:
        mode = "no cut adjustment"
    if fitted and "skipped" in fitted:
        log(f"Note: {fitted['skipped']}")
    m["_automix"] = {
        "enabled": settings["enabled"], "duration": shifted["duration"], "file": mix.name,
        "songs": [{"file": p.name, "start": round(x["start"] - start, 3), "end": round(x["end"] - start, 3),
                   "tempo": x["tempo"], "energy": x["energy"], "gain_db": x["gain_db"]}
                  for p, x in zip(paths, tl["songs"])],
        "splices": [{**sp, "t": round(sp["t"] - start, 3), "start": round(sp["start"] - start, 3),
                     "end": round(sp["end"] - start, 3)} for sp in automix.splices(mix_plan)],
    }
    m["_automix_timeline"] = shifted
    matched = sum(1 for t in mix_plan["transitions"] if t["matched"])
    log(f"Music: playlist of {len(paths)} songs → AutoMix {shifted['duration']:.1f} s "
        f"({matched}/{len(mix_plan['transitions'])} transitions beat-matched) — {mode}"
        + (" — NOT mixed (--no-music)" if no_music else ""))
    for x in tl["songs"]:
        log(f"  {x['start']:7.1f}–{x['end']:7.1f} s  {x['name']}  {x['tempo']:g} BPM  energy {x['energy']:.2f}")
    return None if no_music else mix


def download_music(svc: Services, query: str) -> tuple[Path, dict]:
    """Downloads a song (first YouTube result or URL) into the workspace music folder and analyzes it."""
    dest = svc.projects.music_dir()
    path = svc.downloader.download(query, dest)
    return path, music_analysis(svc, path, dest)
