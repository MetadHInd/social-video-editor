"""Use cases around music: analysis (cached), start offset + beat alignment, playlists (AutoMix),
photo durations fitted to the music, download, and music the engine composes (music.file: compose)."""
from __future__ import annotations

from pathlib import Path

from .. import __version__
from ..domain import automix
from ..domain import compose as dc
from ..domain.cutaways import output_time, resolve, sentence_spans
from ..domain.models import PlanError, Project, UserError
from ..domain.script import split_sentences
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
    if dc.is_compose(cfg["music"]):
        return prepare_composed(svc, project, plan, cfg, infos, no_music)
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


# ── music the engine composes (music.file: compose) ─────────────────────────

COMPOSE_DIR = "compose"            # in the project cache: <preset>_<seed>_<hash>.flac + .origin.json
ORIGIN_SUFFIX = ".origin.json"


def preset_for(svc: Services, cfg: dict, name: str | None = None) -> tuple[str, dict]:
    """The preset to compose with: `name`, else music.compose.preset, else the pack's default
    (graphics.pack → pack.yaml music.preset), else the engine's (upbeat). A pack's own presets
    (music.presets) are available by name."""
    c = dc.settings(cfg.get("music"))
    pack = (cfg.get("graphics") or {}).get("pack")
    music = svc.synth.pack_presets(str(pack)) if pack and svc.synth is not None else {}
    chosen = name or c.get("preset") or music.get("preset")
    try:
        return dc.resolve_preset(chosen, music.get("presets") or {})
    except ValueError as e:
        raise UserError(str(e)) from e


def synth_settings(preset: dict, c: dict) -> dict:
    return {"mix": preset.get("mix") or {}, "pad": preset.get("pad") or {}, "lufs": float(c.get("lufs", -16.0))}


def video_moments(svc: Services, project: Project, plan: dict, cfg: dict, tl) -> dict:
    """What the composer follows, in seconds of the final video: the sentences (start = their first
    word), the full-screen cutaways, the script's arc headings and the cuts."""
    spans = sentence_spans(plan, tl)
    sentences = []
    for s in plan.get("sentences") or []:
        n = s.get("n")
        if n is None or int(n) not in spans:
            continue
        a, b = spans[int(n)]
        start = a
        words = s.get("words") or []
        if words and s.get("source") is not None:
            t = output_time(tl, int(s["source"]), float(words[0][1]))
            if t is not None and a - 0.05 <= t < b:
                start = t
        sentences.append({"n": int(n), "start": round(start, 4), "end": round(b, 4), "text": s.get("text", "")})
    try:
        placed, _ = resolve(plan, tl, int(cfg["output"]["fps"]))
        fullscreen = [(c.start, c.end) for c in placed if not c.overlay]
    except PlanError:
        fullscreen = []
    marks = {}
    if sentences:
        try:
            _, text = svc.projects.read_script(project)
            marks = dc.arc_marks(text, split_sentences)
        except UserError:
            marks = {}
    return {"sentences": sentences, "fullscreen": fullscreen, "marks": marks,
            "cuts": [c.out_start for c in list(tl)[1:]]}


def compose_for_video(svc: Services, project: Project, plan: dict, cfg: dict, tl,
                      preset_name: str | None = None) -> tuple[Path, dict, bool]:
    """The track composed for this plan's timing → (file, score, made now). Cached in
    cache/compose/ by preset + seed + tempo + sections: the same timing reuses the file, a new timing
    makes a new one. Next to it, the proof of origin (<file>.origin.json)."""
    c = dc.settings(cfg.get("music"))
    name, preset = preset_for(svc, cfg, preset_name)
    seed = dc.seed_of(c.get("seed"), project.name)
    total = float(tl.total)
    moments = video_moments(svc, project, plan, cfg, tl)
    sections = dc.arc_sections(total, moments["sentences"], moments["fullscreen"], moments["marks"],
                               moments["cuts"], silences=bool(c.get("silences", True)))
    accents = [a for a, _ in moments["fullscreen"]] if c.get("accents", True) else []
    try:
        score = dc.compose(preset, seed, sections, total, c.get("bpm"), name, accents)
    except ValueError as e:
        raise UserError(str(e)) from e
    key = dc.cache_key(preset, seed, sections, total, c.get("bpm"), accents)
    path = project.cache / COMPOSE_DIR / f"{name}_{seed}_{key}.flac"
    proof = path.with_name(path.stem + ORIGIN_SUFFIX)
    made = not (svc.projects.exists(path) and svc.projects.exists(proof))
    if made:
        audio = svc.synth.render(score, path, synth_settings(preset, c))
        svc.projects.write_json(proof, dc.origin(score, __version__, audio, preset, cfg.get("language", "en"),
                                                 {"marks": len(moments["marks"]),
                                                  "sentences": len(moments["sentences"]),
                                                  "fullscreen": len(moments["fullscreen"])}))
    return path, score, made


def prepare_composed(svc: Services, project: Project, plan: dict, cfg: dict, infos: list[dict],
                     no_music: bool) -> Path | None:
    """music.file: compose — the engine composes the track for this video (compose_for_video), then
    the cuts follow the music settings as with any track (align / cuts_on_beat) and every shot gets
    the composed beats for beat_pulse. The track starts with the video (music._start = 0) and its own
    entry hit is kept (music.fade_in at most 0.02 s)."""
    m = cfg["music"]
    tl = compute_timeline(plan, cfg)
    path, score, made = compose_for_video(svc, project, plan, cfg, tl)
    beats = score["beats"]
    durations = [i["duration"] for i in infos]
    if any("beats" in s for s in plan["shots"]) or m.get("align") == "exact":
        align_to_beats(plan, beats, cfg, durations)
        mode = "every cut on a beat"
    elif m.get("cuts_on_beat", True):
        mode = f"{snap_cuts_to_beats(plan, beats, cfg, durations)} cuts moved to the beat"
    else:
        mode = "the music follows the cuts"
    for clip in compute_timeline(plan, cfg):
        a, b = clip.out_start, clip.out_start + clip.dur
        clip.data["_beats"] = [round(t - a, 3) for t in beats if a + 0.05 < t < b - 0.05]
    m["_start"] = 0.0
    m["fade_in"] = min(float(m.get("fade_in", 0.25) or 0.0), 0.02)
    m["_composed"] = {"file": path.name, "origin": path.stem + ORIGIN_SUFFIX, "preset": score["preset"],
                      "seed": score["seed"], "bpm": score["bpm"], "key": f"{score['key']} {score['mode']}",
                      "sections": [[s["kind"], s["start"], s["end"]] for s in score["sections"]]}
    log(f"Music: composed by the engine ({'new' if made else 'cached'}: {path.name}) — {mode}"
        + (" — NOT mixed (--no-music)" if no_music else ""))
    for line in dc.section_lines(score):
        log(line)
    return None if no_music else path


def compose_track(svc: Services, out: Path, seconds: float, preset_name: str | None = None, seed=None,
                  bpm: float | None = None, cfg: dict | None = None) -> tuple[Path, dict]:
    """`sve music --compose`: a standalone track of `seconds` (the arc of an explainer in whole bars:
    intro, verse, silence, chorus, silence, emotive outro) → (file, proof of origin). A .m4a / .mp3 /
    .ogg `out` also keeps the lossless .flac the proof describes."""
    cfg = cfg or {}
    c = dc.settings(cfg.get("music"))
    name, preset = preset_for(svc, cfg, preset_name)
    seed = dc.seed_of(seed if seed is not None else c.get("seed"), f"{name}:{seconds:g}")
    tempo = float(bpm or c.get("bpm") or preset["bpm"])
    try:
        score = dc.compose(preset, seed, dc.template_sections(seconds, tempo), seconds, tempo, name)
    except ValueError as e:
        raise UserError(str(e)) from e
    out = Path(out)
    lossless = out if out.suffix.lower() in (".flac", ".wav") else out.with_suffix(".flac")
    audio = svc.synth.render(score, lossless, synth_settings(preset, c))
    if lossless != out:
        svc.synth.encode(lossless, out)
    proof = dc.origin(score, __version__, audio, preset, cfg.get("language", "en"))
    svc.projects.write_json(lossless.with_name(lossless.stem + ORIGIN_SUFFIX), proof)
    return out, {**proof, "score": score}
