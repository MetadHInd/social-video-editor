#!/usr/bin/env python3
"""Measure how fast the engine renders realistic footage, so performance changes are measured, not guessed.

Scenarios (synthetic media only; generated once and cached in --media-dir, keyed by their parameters):

  * vertical60   — ~60 s 1080x1920 edit (base recipe, default framing.slow_push) of ~20 shots of ~3 s
                   from 4K HEVC 10-bit vertical phone-like clips (half 30 fps, half 60 fps, AAC audio),
                   beat_pulse on a third of the shots, one zoom_punch, one polaroid and one collage
                   (drawn shots that depend on other segments), a music file, English, no subtitles;
  * slideshow600 — ~10 min 1920x1080 slideshow (slideshow recipe) of 133 photos of 12 MP with an
                   AutoMix playlist of 3 synthesized tracks (`sve plan`, then `sve render`).

Scenarios run only when named (`--scenario analyze` / `--scenario explainer75`; not part of "all"):

  * analyze      — `sve analyze` of 4 synthetic ~30 s 1080x1920 HEVC phone-like clips whose sound is
                   synthetic English speech (macOS `say`; a tone elsewhere): proxies, 16 kHz wavs, image /
                   scene / audio analysis and Whisper (model `small`, already downloaded — the bench runs
                   offline). Every run deletes the project's cache first. Records the transcription engine,
                   model and device that wrote each analysis, the words heard, and the seconds spent in
                   transcription (from the log). `--engine` / `--analysis-jobs` set transcription.engine
                   and analysis.jobs (default: the recipe's) to compare them;
  * explainer75  — a final render of a ~75 s explainer (explainer recipe): one 1080x1920 presenter-like
                   recording (speech via `say`, else a tone), a script of 18 sentences with 6 full-frame
                   graphics (title, bars, number, list, headline, diagram) and subtitles. The words are
                   written to the analysis (evenly timed from the script), so `sve plan` finds every
                   sentence on any machine; no music, no b-roll, no local models. Needs the graphics extra.
                   Every run deletes the shot segments and the cleaned voice (both done once per real render).

--quick swaps in tiny versions (a ~6 s vertical edit from 1080p clips, a ~30 s slideshow of 8 photos)
for a fast sanity check; their results are stored under "<scenario>-quick" so they are never
compared with full runs by mistake (likewise "-draft" for --draft).

What one timed run is: the project is prepared once per scenario (sources probed / proxied / analyzed,
music analyzed and the AutoMix rendered — what `sve plan` / `sve view` leave behind), then every run
deletes the shot segment cache and the previous version folders of the bench's own project and runs
`sve render` as a subprocess, so each run renders every shot into a fresh version folder.

Per run: wall time of `sve render`, render_time_sec and duration_sec from summary.json, seconds of
wall time per second of output, CPU user + sys of the whole process tree (FFmpeg included), peak RSS
of its largest process (POSIX only), approximate stages from the arrival time of the render log
lines and the segment files' modification times (prepare → shots → finish = join + text layer +
mastering + cover), the output size. Results also record FFmpeg / Python / CPU / git revision and
the SVE_JOBS / SVE_HWACCEL / SVE_DECODER_THREADS environment variables.

Usage:
    python scripts/bench.py --quick                          # sanity check (a few minutes)
    python scripts/bench.py --out before.json                # both full scenarios, final quality
    python scripts/bench.py --scenario vertical60 --repeat 3 --warmup --out after.json
    python scripts/bench.py --scenario slideshow600 --draft  # bench draft renders
    python scripts/bench.py --compare before.json after.json # markdown table: A, B, change %
    python scripts/bench.py --prepare-only --workdir /tmp/sve-bench   # media + projects only, no render
    python scripts/bench.py --scenario analyze --repeat 2 --analysis-jobs 1 --out serial.json
    python scripts/bench.py --scenario analyze --repeat 2 --out parallel.json  # then --compare them
    python scripts/bench.py --scenario explainer75 --repeat 1 --out explainer.json  # 75 s final render

A CLI run that takes longer than --timeout (default 3600 s) is killed with its FFmpeg processes and
recorded as an error, so a hung FFmpeg never blocks a measurement slot.

Requires FFmpeg 7+ (with libx265 to generate the clips) and the engine's dependencies. The full
scenarios render minutes of 1080p video: run them on an otherwise idle machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BENCH_VERSION = 1
MEDIA_VERSION = 1                     # bump when the generated media change (invalidates the cache)
SCENARIOS = ("vertical60", "slideshow600")       # --scenario all
EXTRA_SCENARIOS = ("analyze", "explainer75")       # only when named
ENV_VARS = ("SVE_JOBS", "SVE_HWACCEL", "SVE_DECODER_THREADS")
MARKER = ".sve-bench"                 # only workspaces carrying this file are ever cleaned
BPM = 120.0
# metrics summarized (median / min / max) and compared; lower is better for all of them
METRICS = ("wall_sec", "render_time_sec", "sec_per_output_sec", "cpu_sec", "cpu_user_sec", "cpu_sys_sec",
           "max_rss_mb", "stage_prepare_sec", "stage_shots_sec", "stage_finish_sec", "duration_sec", "output_mb",
           "transcription_sec", "words")
COMPARE_METRICS = ("wall_sec", "render_time_sec", "sec_per_output_sec", "cpu_sec", "max_rss_mb",
                   "stage_prepare_sec", "stage_shots_sec", "stage_finish_sec", "duration_sec", "transcription_sec")
LINE_RE = re.compile(r"^\[sve [0-9:]+\] (.*)$")
VIEW_RE = re.compile(r" — (\d+) shots — ([0-9.]+) s$")                 # first line of `sve view`
VERSION_DIR_RE = re.compile(r"v\d+.*")       # v3, v3Draft, v3_failed…

TRANSITIONS = ["cut", "cut", "zoom", "cut", "whip", "cut"]          # the base recipe's rotation

# Scenario sizes. Clip windows are whole beats at BPM, so beat alignment keeps every source window.
PARAMS = {
    "vertical60": {
        "clips": {"count": 8, "W": 2160, "H": 3840, "dur": 6.0, "rates": [30, 60]},
        "music": {"bpm": BPM, "bars": 36, "root_hz": 220.0, "seed": 1},
        "shots": {"videos": 18, "beats": 6, "polaroid_beats": 4, "collage_beats": 8},
    },
    "vertical60-quick": {
        "clips": {"count": 2, "W": 1080, "H": 1920, "dur": 4.0, "rates": [30, 60]},
        "music": {"bpm": BPM, "bars": 8, "root_hz": 220.0, "seed": 1},
        "shots": {"videos": 4, "beats": 2, "polaroid_beats": 2, "collage_beats": 2},
    },
    "slideshow600": {
        "photos": {"count": 133, "landscape": [4032, 3024], "portrait": [3024, 4032]},
        # tempos within ±8 % (beat-matched AutoMix), ~205 s each → a ~10 min mix
        "tracks": [{"bpm": 120.0, "bars": 102, "root_hz": 196.0, "seed": 2},
                   {"bpm": 124.0, "bars": 106, "root_hz": 233.1, "seed": 3},
                   {"bpm": 118.0, "bars": 100, "root_hz": 174.6, "seed": 4}],
    },
    "slideshow600-quick": {
        "photos": {"count": 8, "landscape": [1600, 1200], "portrait": [1200, 1600]},
        "tracks": [{"bpm": 120.0, "bars": 12, "root_hz": 196.0, "seed": 2},
                   {"bpm": 124.0, "bars": 12, "root_hz": 233.1, "seed": 3}],
    },
    "analyze": {"talks": {"count": 4, "W": 1080, "H": 1920, "fps": 30, "dur": 30.0}},
    "analyze-quick": {"talks": {"count": 2, "W": 1080, "H": 1920, "fps": 30, "dur": 8.0}},
    "explainer75": {"presenter": {"W": 1080, "H": 1920, "fps": 30, "dur": 75.0}, "sentences": 18, "graphics": 6},
    "explainer75-quick": {"presenter": {"W": 720, "H": 1280, "fps": 30, "dur": 20.0}, "sentences": 5, "graphics": 2},
}

# Synthetic narration (generic sentences written for the bench), spoken by `say` where it exists.
SENTENCES = [
    "A bicycle stays up because the wheels keep turning under you.",
    "When you lean to one side, the front wheel steers into the fall.",
    "That small correction happens many times every second.",
    "Most of the effort goes into pushing air out of the way.",
    "At twenty kilometres an hour, the air takes almost half of your energy.",
    "Tyres with the right pressure roll with much less friction.",
    "A clean chain wastes very little of the force from your legs.",
    "Brakes turn your speed into heat in the pads and the rims.",
    "Gears let your legs spin at the same pace on a hill or on the flat.",
    "A lighter frame helps on climbs, but it matters less than you think.",
    "Riding in a group cuts the wind for everyone behind the leader.",
    "Cities that add bike lanes see more people riding every year.",
    "A short ride to work can replace a lot of time in traffic.",
    "Check the tyres once a week and oil the chain once a month.",
    "Brake early before a corner, never in the middle of it.",
    "Lights in front and behind make you visible from far away.",
    "So the next time you ride, remember the small corrections that keep you up.",
    "Tell me which of these you did not know.",
]
GRAPHIC_CUES = [
    'title text="How a bicycle|stays up"',
    'bars title="Energy per kilometre" data="Walking:60, Cycling:20, Car:500" unit=" kcal"',
    'number value="40" unit="%" label="of the effort goes into the air"',
    'list title="Three habits" items="Check the tyres; Oil the chain; Brake early" marks="n"',
    'headline text="City adds fifty kilometres of bike lanes" outlet="The Example Daily" highlight="fifty kilometres"',
    'diagram nodes="Pedal; Chain; Wheel; Road" edges="Pedal>Chain, Chain>Wheel, Wheel>Road"',
]


class BenchError(RuntimeError):
    """A step of the benchmark failed (the message says which and where its log is)."""


# ── arguments ────────────────────────────────────────────────────────────────

def default_media_dir() -> Path:
    """A per-user cache folder: the generated media (≈ 1 GB for the full scenarios) survive between runs."""
    base = os.environ.get("XDG_CACHE_HOME") or (os.environ.get("LOCALAPPDATA") if os.name == "nt" else None)
    return (Path(base) if base else Path.home() / ".cache") / "sve-bench" / "media"


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__[__doc__.index("Usage:"):])
    ap.add_argument("--scenario", choices=(*SCENARIOS, *EXTRA_SCENARIOS, "all"), default="all",
                    help=f"what to run (default: all = {', '.join(SCENARIOS)})")
    ap.add_argument("--quick", action="store_true", help="tiny versions of the scenarios (sanity check)")
    ap.add_argument("--repeat", type=int, default=1, metavar="N", help="timed runs per scenario (median, min, max)")
    ap.add_argument("--warmup", action="store_true", help="one extra, discarded run before the timed ones")
    ap.add_argument("--draft", action="store_true", help="bench draft renders (half resolution) instead of final")
    ap.add_argument("--media-dir", type=Path, default=None,
                    help=f"cache of the generated media (default: {default_media_dir()})")
    ap.add_argument("--workdir", type=Path, default=None,
                    help="where the bench workspaces go (kept; default: a temporary folder, deleted)")
    ap.add_argument("--out", type=Path, default=None,
                    help="results JSON (default: <media-dir>/../results/<time>-<git rev>.json)")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("A.json", "B.json"),
                    help="print a markdown table comparing two results files (renders nothing)")
    ap.add_argument("--timeout", type=float, default=3600, metavar="SEC",
                    help="kill a CLI run (and its FFmpeg processes) after SEC seconds and report it as a failure; "
                         "0 = never (default: 3600)")
    ap.add_argument("--prepare-only", action="store_true",
                    help="generate the media and prepare the projects, render nothing (use with --workdir)")
    ap.add_argument("--quiet", action="store_true", help="do not echo the engine's log")
    ap.add_argument("--engine", default=None, metavar="NAME",
                    help="analyze: transcription.engine (faster-whisper, mlx, auto; default: the recipe's)")
    ap.add_argument("--analysis-jobs", default=None, metavar="N",
                    help="analyze: analysis.jobs (auto or a number; default: the recipe's)")
    a = ap.parse_args(argv)
    if a.repeat < 1:
        ap.error("--repeat must be at least 1")
    a.media_dir = a.media_dir or default_media_dir()
    return a


def scenario_keys(a: argparse.Namespace) -> list[str]:
    """Result keys of the scenarios to run: "vertical60", "vertical60-quick", "slideshow600-draft"…"""
    names = SCENARIOS if a.scenario == "all" else (a.scenario,)
    return [n + ("-quick" if a.quick else "") + ("-draft" if a.draft else "") for n in names]


# ── statistics and comparison (pure) ─────────────────────────────────────────

def summarize(runs: list[dict]) -> dict:
    """median / min / max of every metric present in the runs."""
    out = {}
    for m in METRICS:
        vals = [r[m] for r in runs if isinstance(r.get(m), (int, float))]
        if vals:
            out[m] = {"median": round(statistics.median(vals), 3), "min": round(min(vals), 3),
                      "max": round(max(vals), 3), "n": len(vals)}
    return out


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:.0f}" if abs(v) >= 1000 else f"{v:.2f}"


def change_pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return (b - a) / a * 100.0


def _label(res: dict, path: str) -> str:
    env = res.get("env") or {}
    rev = env.get("git_rev") or "?"
    return f"{path} (git {rev}{'+dirty' if env.get('git_dirty') else ''})"


def compare_table(a: dict, b: dict, a_name: str = "A", b_name: str = "B") -> str:
    """Markdown table of the median of every compared metric in the scenarios of A and/or B."""
    lines = [f"A = {_label(a, a_name)}", f"B = {_label(b, b_name)}", "",
             "| scenario | metric | A | B | change |", "|---|---|---:|---:|---:|"]
    sa, sb = a.get("scenarios") or {}, b.get("scenarios") or {}
    for key in [*sa, *(k for k in sb if k not in sa)]:
        stats_a, stats_b = (sa.get(key) or {}).get("stats") or {}, (sb.get(key) or {}).get("stats") or {}
        for m in COMPARE_METRICS:
            va, vb = (stats_a.get(m) or {}).get("median"), (stats_b.get(m) or {}).get("median")
            if va is None and vb is None:
                continue
            pct = change_pct(va, vb)
            lines.append(f"| {key} | {m} | {_fmt(va)} | {_fmt(vb)} | "
                         f"{'n/a' if pct is None else f'{pct:+.1f} %'} |")
    return "\n".join(lines)


# ── environment ──────────────────────────────────────────────────────────────

def _first_line(cmd: list[str], cwd: Path | None = None) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=cwd, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out.splitlines()[0] if r.returncode == 0 and out else None


def environment() -> dict:
    from _synth import REPO

    init = REPO / "src" / "social_video_editor" / "__init__.py"
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8")) if init.exists() else None
    status = None
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", cwd=REPO, timeout=30)
        status = bool(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "engine_version": m.group(1) if m else None,
        "git_rev": _first_line(["git", "rev-parse", "--short", "HEAD"], REPO), "git_dirty": status,
        "ffmpeg": _first_line(["ffmpeg", "-hide_banner", "-version"]) or _first_line(["ffmpeg", "-version"]),
        "python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "env": {k: os.environ.get(k) for k in ENV_VARS},
    }


# ── media (generated once, cached by parameters) ─────────────────────────────

def cached_media(media_dir: Path, kind: str, params: dict, build) -> tuple[Path, float, bool]:
    """Folder with the media `build(folder)` generates for `params`: (path, seconds spent, was cached).

    Built in a hidden `.partial` folder and renamed when complete, so an interrupted generation is
    never mistaken for a finished one."""
    key = hashlib.sha1(json.dumps({"v": MEDIA_VERSION, "kind": kind, **params}, sort_keys=True).encode()).hexdigest()
    final = media_dir / f"{kind}-{key[:12]}"
    if (final / "params.json").exists():
        return final, 0.0, True
    partial = media_dir / f".{kind}-{key[:12]}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    print(f"── generating {kind} media in {final} …", flush=True)
    t0 = time.monotonic()
    build(partial)
    (partial / "params.json").write_text(json.dumps({"kind": kind, **params}, indent=2), encoding="utf-8")
    partial.rename(final)
    took = time.monotonic() - t0
    print(f"   {kind} media ready in {took:.1f} s", flush=True)
    return final, took, False


def vertical_media(media_dir: Path, p: dict) -> tuple[list[Path], Path, float]:
    from _synth import make_phone_clips, synth_track

    c, mu = p["clips"], p["music"]
    clips_dir, t1, _ = cached_media(media_dir, "clips", c, lambda d: make_phone_clips(
        d / "clips", c["count"], c["W"], c["H"], c["dur"], tuple(c["rates"])))
    music_dir, t2, _ = cached_media(media_dir, "music", mu, lambda d: synth_track(
        d / "music" / f"loop_{mu['bpm']:g}.wav", mu["bpm"], mu["bars"], mu["root_hz"], mu["seed"]))
    clips = sorted((clips_dir / "clips").glob("*.mov"))
    return clips, next((music_dir / "music").glob("*.wav")), t1 + t2


def slideshow_media(media_dir: Path, p: dict) -> tuple[Path, Path, float]:
    from _synth import make_photos, synth_track

    ph = p["photos"]
    photos_dir, t1, _ = cached_media(media_dir, "photos", ph, lambda d: make_photos(
        d / "photos", ph["count"], tuple(ph["landscape"]), tuple(ph["portrait"]), minutes_apart=5))

    def tracks(d: Path) -> None:
        for k, t in enumerate(p["tracks"]):
            synth_track(d / "playlist" / f"{k + 1:02d}_track_{t['bpm']:g}.wav", t["bpm"], t["bars"],
                        t["root_hz"], t["seed"])

    playlist_dir, t2, _ = cached_media(media_dir, "playlist", {"tracks": p["tracks"]}, tracks)
    return photos_dir / "photos", playlist_dir / "playlist", t1 + t2


# ── projects ─────────────────────────────────────────────────────────────────

def vertical_plan(n_clips: int, clip_dur: float, videos: int, beats: int, polaroid_beats: int,
                  collage_beats: int, bpm: float = BPM) -> dict:
    """A hand-written plan like an editor would make: `videos` shots of `beats` beats taken from the
    clips in turn (every shot a different window: no two shots share a segment), a zoom_punch on the
    hook, beat_pulse on every third shot, a polaroid after the first third and a collage after the
    second third (both depend on earlier segments), a hook and a CTA text."""
    win = beats * 60.0 / bpm
    per_clip = -(-videos // n_clips)
    hi = max(0.3, clip_dur - 0.3 - win)
    starts = [round(0.3 + (hi - 0.3) * k / max(1, per_clip - 1), 3) for k in range(per_clip)]
    shots: list[dict] = []
    video_pos: list[int] = []
    polaroid_after, collage_after = max(1, videos // 3), max(2, 2 * videos // 3)
    for k in range(videos):
        s = starts[k // n_clips]
        shot = {"source": k % n_clips, "start": s, "end": round(s + win, 3), "beats": beats,
                "transition": TRANSITIONS[k % len(TRANSITIONS)]}
        if k == 0:
            shot.update(role="hook", effects=[{"type": "zoom_punch", "t": 0}])
        elif k % 3 == 1:
            shot["effects"] = [{"type": "beat_pulse"}]
        if k == videos - 1:
            shot["role"] = "ending"
        video_pos.append(len(shots))
        shots.append(shot)
        if k + 1 == polaroid_after:
            shots.append({"type": "polaroid", "from_shot": video_pos[-1], "beats": polaroid_beats,
                          "text": "Benchmark", "transition": "cut"})
        if k + 1 == collage_after:
            refs = video_pos[-4:]
            shots.append({"type": "collage", "from_shots": refs, "background_from": refs[-1],
                          "beats": collage_beats, "transition": "zoom"})
    return {"shots": shots,
            "texts": [{"text": "Synthetic benchmark", "shot": 0, "style": "hook"},
                      {"text": "Rendered from a plan.json", "shot": len(shots) - 1, "style": "cta"}]}


def setup_vertical(workspace: Path, media_dir: Path, p: dict) -> tuple[str, float]:
    from _synth import write_project

    clips, music, media_sec = vertical_media(media_dir, p)
    c, sh = p["clips"], p["shots"]
    plan = vertical_plan(len(clips), c["dur"], sh["videos"], sh["beats"], sh["polaroid_beats"],
                         sh["collage_beats"], p["music"]["bpm"])
    settings = {"language": "en", "subtitles": {"auto": False}, "duration": {"target": 60, "max": 90},
                "music": {"file": str(music), "start_at": 0.0}}
    write_project(workspace, "bench_vertical", "base", [str(x) for x in clips], settings, plan)
    return "bench_vertical", media_sec


def setup_slideshow(workspace: Path, media_dir: Path, p: dict) -> tuple[str, float]:
    from _synth import write_project

    album, playlist, media_sec = slideshow_media(media_dir, p)
    settings = {"language": "en", "music": {"playlist": [str(playlist)], "automix": {"enabled": True}}}
    write_project(workspace, "bench_slideshow", "slideshow", [str(album)], settings)
    return "bench_slideshow", media_sec


# ── speech scenarios (analyze, explainer75) ─────────────────────────────────

def speech_kind() -> str:
    """"say" where macOS speech synthesis exists, else "tone" (part of the media cache key)."""
    return "say" if shutil.which("say") else "tone"


def narration(k: int, dur: float, words_per_sec: float = 2.4) -> str:
    """Sentences from SENTENCES (starting at the k-th, in turn) worth about `dur` seconds of speech."""
    out, n, i = [], 0, k
    while n < dur * words_per_sec:
        sentence = SENTENCES[i % len(SENTENCES)]
        out.append(sentence)
        n, i = n + len(sentence.split()), i + 1
    return " ".join(out)


def speech_wav(path: Path, text: str, dur: float, tone_hz: float = 220.0) -> Path:
    """`dur` seconds of mono 48 kHz WAV: `text` spoken by `say` (padded / cut to `dur`), or a tone."""
    from _synth import ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    if speech_kind() == "say":
        aiff = path.with_suffix(".aiff")
        for voice in (["-v", "Samantha"], []):                    # a common English voice, else the default
            if subprocess.run(["say", *voice, "-o", str(aiff), text], capture_output=True).returncode == 0:
                break
        else:
            raise BenchError("macOS `say` could not synthesize the narration")
        ffmpeg("-i", aiff, "-af", "apad", "-t", dur, "-ac", "1", "-ar", 48000, path)
        aiff.unlink()
    else:
        ffmpeg("-f", "lavfi", "-i", f"sine=f={tone_hz}:sample_rate=48000:d={dur}", "-ac", "1", path)
    return path


def make_talking_clips(folder: Path, texts: list[str], W: int, H: int, fps: int, dur: float) -> list[Path]:
    """Phone-like vertical HEVC 10-bit clips (testsrc2 + noise, hue per clip) whose sound is each text spoken."""
    from _synth import ffmpeg

    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, text in enumerate(texts):
        wav = speech_wav(folder / f"_voice_{i + 1}.wav", text, dur, 220.0 + 55 * i)
        clip = folder / f"TALK_{i + 1:04d}.mov"
        ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={fps}:d={dur},hue=h={i * 47 % 360},noise=alls=6:allf=t",
               "-i", wav, "-map", "0:v", "-map", "1:a", "-t", dur,
               "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast",
               "-x265-params", "log-level=error", "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "160k", clip)
        wav.unlink()
        out.append(clip)
    return out


def setup_analyze(workspace: Path, media_dir: Path, p: dict, a: argparse.Namespace) -> tuple[str, float]:
    from _synth import write_project

    t = p["talks"]
    params = {**t, "speech": speech_kind()}
    texts = [narration(3 * k, t["dur"]) for k in range(t["count"])]
    folder, media_sec, _ = cached_media(media_dir, "talks", params, lambda d: make_talking_clips(
        d / "talks", texts, t["W"], t["H"], t["fps"], t["dur"]))
    settings: dict = {"language": "en", "subtitles": {"auto": False}}
    if a.engine:
        settings["transcription"] = {"engine": a.engine}
    if a.analysis_jobs:
        settings["analysis"] = {"jobs": int(a.analysis_jobs) if str(a.analysis_jobs).isdigit() else a.analysis_jobs}
    clips = sorted((folder / "talks").glob("*.mov"))
    write_project(workspace, "bench_analyze", "base", [str(c) for c in clips], settings)
    return "bench_analyze", media_sec


def explainer_script(n_sentences: int, n_graphics: int) -> tuple[str, list[str]]:
    """(script.md, its sentences): a graphic cue before every few sentences, the presenter in between."""
    sentences = [SENTENCES[i % len(SENTENCES)] for i in range(n_sentences)]
    every = max(2, n_sentences // max(1, n_graphics))
    lines, g, on_graphic = ["# synthetic explainer (scripts/bench.py)"], 0, False
    for i, sentence in enumerate(sentences):
        if i % every == 1 and g < n_graphics:            # a graphic over one sentence…
            lines.append(f"[{GRAPHIC_CUES[g % len(GRAPHIC_CUES)]}]")
            g, on_graphic = g + 1, True
        elif on_graphic:                                  # …then the presenter again
            lines.append("[camera]")
            on_graphic = False
        lines.append(sentence)
    return "\n".join(lines) + "\n", sentences


def timed_words(sentences: list[str], dur: float, lead: float = 0.5, gap: float = 0.45) -> list[dict]:
    """Every word of the sentences, evenly timed over the recording (as if read at a steady pace)."""
    n = sum(len(s.split()) for s in sentences)
    slot = (dur - 2 * lead - gap * (len(sentences) - 1)) / max(1, n)
    words, t = [], lead
    for sentence in sentences:
        for w in sentence.split():
            words.append({"word": w, "start": round(t, 3), "end": round(t + 0.8 * slot, 3), "prob": 1.0})
            t += slot
        t += gap
    return words


def setup_explainer(workspace: Path, media_dir: Path, p: dict, a: argparse.Namespace) -> tuple[str, float]:
    from _synth import write_project

    pr = p["presenter"]
    script, sentences = explainer_script(p["sentences"], p["graphics"])
    params = {**pr, "speech": speech_kind(), "sentences": p["sentences"]}
    folder, media_sec, _ = cached_media(media_dir, "presenter", params, lambda d: make_talking_clips(
        d / "presenter", [" ".join(sentences)], pr["W"], pr["H"], pr["fps"], pr["dur"]))
    clip = next((folder / "presenter").glob("*.mov"))
    settings = {"language": "en", "script": {"auto_broll": False}, "decider": {"enabled": False}}
    d = write_project(workspace, "bench_explainer", "explainer", [str(clip)], settings)
    (d / "script.md").write_text(script, encoding="utf-8")
    return "bench_explainer", media_sec


def write_script_words(project: Path, sentences: list[str], dur: float) -> int:
    """Replace the words of the (single) source's cached analysis with the script read evenly, so
    `sve plan` finds every sentence whatever Whisper heard (or did not: a tone). → words written."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from social_video_editor.domain.selection import voice_segments

    found = [f for f in (project / "cache").glob("*_analysis.json")]
    if len(found) != 1:
        raise BenchError(f"expected one analysis in {project / 'cache'}, found {len(found)}")
    data = json.loads(found[0].read_text(encoding="utf-8"))
    data["words"] = timed_words(sentences, dur)
    segs = voice_segments(data["words"])
    data["voice"] = {"segments": segs, "ratio": round(sum(b - a for a, b in segs) / max(dur, 1e-3), 3)}
    # a presenter's face, as the face detector finds it in a real recording (testsrc2 has none, and a
    # source without one would be searched again at every render)
    data.setdefault("face", {"x": 0.5, "y": 0.36, "size": 0.24, "eyes": 0.33, "ratio": 0.95,
                             "frames": int(dur), "detector": "bench"})
    found[0].write_text(json.dumps(data), encoding="utf-8")
    return len(data["words"])


SOURCE_LINE_RE = re.compile(r"^\s*(transcription|words) \((.+?)\)")


def transcription_seconds(lines: list[tuple[float, str]]) -> float | None:
    """Seconds between each source's "transcription (name)…" and its "words (name): N" log lines, summed
    (both are written while the source holds the Whisper lock, so the spans never overlap)."""
    started, total, seen = {}, 0.0, False
    for t, line in lines:
        m = LINE_RE.match(line)
        m = m and SOURCE_LINE_RE.match(m.group(1))
        if not m:
            continue
        if m.group(1) == "transcription":
            started[m.group(2)] = t
        elif m.group(2) in started:
            total, seen = total + t - started.pop(m.group(2)), True
    return round(total, 3) if seen else None


def analysis_summary(project: Path) -> dict:
    """Engines / models / devices that wrote the cached analyses, and the words they heard."""
    engines, words = set(), 0
    for f in sorted((project / "cache").glob("*_analysis.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        words += len(data.get("words") or [])
        tr = data.get("transcription") or {}
        engines.add((tr.get("engine") or "faster-whisper (not recorded)", tr.get("model"), tr.get("device")))
    return {"words": words, "transcription": [dict(zip(("engine", "model", "device"), e)) for e in sorted(engines, key=str)]}


def timed_analyze(workspace: Path, project: Path, a: argparse.Namespace, log_path: Path) -> dict:
    """Delete the project's whole cache, then `sve analyze` (proxies, wavs, analysis, transcription)."""
    clean_project(workspace, project, cache=True)
    run = run_sve(workspace, ["analyze", project.name], log_path, a.quiet, a.timeout)
    res = {k: run[k] for k in ("wall_sec", "cpu_user_sec", "cpu_sys_sec", "cpu_sec", "max_rss_mb") if k in run}
    res.update({"transcription_sec": transcription_seconds(run["lines"]), "log": str(log_path),
                **analysis_summary(project)})
    return res


# ── running the engine ───────────────────────────────────────────────────────

def clean_project(workspace: Path, project: Path, cache: bool = False, extra: tuple[str, ...] = ()) -> None:
    """Remove the version folders and the shot segment cache of a bench project — and nothing else
    (`extra`: more cache subfolders, e.g. "voice"; `cache=True`: the whole cache folder).

    Refuses unless the workspace carries the bench marker and the project lies inside it, so a
    --workdir pointing at a real workspace can never lose a render."""
    workspace, project = workspace.resolve(), project.resolve()
    if not (workspace / MARKER).is_file() or workspace not in project.parents:
        raise BenchError(f"refusing to clean {project}: not inside a bench workspace")
    for d in project.iterdir():
        if d.is_dir() and VERSION_DIR_RE.fullmatch(d.name):
            shutil.rmtree(d)
    for name in ("", ) if cache else ("segments", *extra):
        folder = project / "cache" / name if name else project / "cache"
        if folder.is_dir():
            shutil.rmtree(folder)


def _maxrss_mb(ru) -> float | None:
    rss = getattr(ru, "ru_maxrss", 0)
    if not rss:
        return None
    return round(rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024, 1)   # bytes on macOS, KB elsewhere


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill `sve` and every FFmpeg it started (they share its stdout pipe: killing only the CLI would
    leave the pipe open). POSIX: its own process group; Windows: taskkill /T."""
    try:
        if os.name == "posix":
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def run_sve(workspace: Path, args: list[str], log_path: Path, quiet: bool = False, timeout: float = 0) -> dict:
    """Run `sve <args>`; returns wall / CPU / RSS and the arrival time of every log line.

    CPU: RUSAGE_CHILDREN delta around the run (descendants are included once reaped, so FFmpeg counts).
    Peak RSS: from os.wait4 for this child (the largest process of its tree). Both are skipped where
    the platform has no `resource` / `os.wait4` (Windows). `timeout` > 0: a run still going after that
    many seconds (a hung FFmpeg) is killed with its whole process tree and reported as a failure."""
    import threading

    from _synth import sve_command, sve_env

    try:
        import resource
    except ImportError:                    # Windows
        resource = None
    before = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    lines: list[tuple[float, str]] = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    m0 = time.monotonic()
    timed_out = threading.Event()
    with open(log_path, "w", encoding="utf-8") as log:
        env = {**sve_env(workspace), "PYTHONUNBUFFERED": "1"}             # log lines arrive as they are printed
        proc = subprocess.Popen(sve_command(*args), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                **({"start_new_session": True} if os.name == "posix" else {}))
        timer = threading.Timer(timeout, lambda: (timed_out.set(), _kill_tree(proc))) if timeout > 0 else None
        if timer:
            timer.daemon = True
            timer.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                now = time.time()
                lines.append((now, line.rstrip("\n")))
                log.write(f"{now - t0:9.3f} {line}")
                if not quiet:
                    print(f"    {line}", end="", flush=True)
            proc.stdout.close()
            ru = None
            if hasattr(os, "wait4"):
                _, status, ru = os.wait4(proc.pid, 0)
                proc.returncode = os.waitstatus_to_exitcode(status)
            else:
                proc.wait()
        except BaseException:              # Ctrl-C: the child is in its own session, stop it too
            _kill_tree(proc)
            raise
        finally:
            if timer:
                timer.cancel()
    wall = time.monotonic() - m0
    if timed_out.is_set():
        raise BenchError(f"`sve {' '.join(args)}` killed after {timeout:g} s (hung?); log: {log_path}")
    if proc.returncode != 0:
        raise BenchError(f"`sve {' '.join(args)}` failed (exit {proc.returncode}); log: {log_path}")
    out: dict = {"wall_sec": round(wall, 3), "t_start": t0, "t_end": t0 + wall, "lines": lines}
    if resource and before is not None:
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        out["cpu_user_sec"] = round(after.ru_utime - before.ru_utime, 3)
        out["cpu_sys_sec"] = round(after.ru_stime - before.ru_stime, 3)
        out["cpu_sec"] = round(out["cpu_user_sec"] + out["cpu_sys_sec"], 3)
    if ru is not None:
        out["max_rss_mb"] = _maxrss_mb(ru)
    return out


def stages(run: dict, segments: Path) -> dict:
    """Approximate stages of a render from when its log lines arrived and when its segments were written:
    prepare = start → "Timeline:" (sources, music, beat alignment), shots = → last segment file written,
    finish = → exit (join with transitions, text layer, sound effects, mastering, cover, extras)."""
    t_timeline = next((t for t, line in run["lines"]
                       if (m := LINE_RE.match(line)) and m.group(1).startswith("Timeline:")), None)
    mtimes = [f.stat().st_mtime for f in segments.glob("*") if f.is_file()] if segments.is_dir() else []
    if t_timeline is None or not mtimes:
        return {}
    t_shots = max(mtimes)
    if not run["t_start"] <= t_timeline <= t_shots <= run["t_end"] + 1:
        return {}
    return {"stage_prepare_sec": round(t_timeline - run["t_start"], 3),
            "stage_shots_sec": round(t_shots - t_timeline, 3),
            "stage_finish_sec": round(max(0.0, run["t_end"] - t_shots), 3)}


def latest_summary(project: Path) -> Path:
    found = sorted(project.glob("v*/summary.json"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise BenchError(f"the render wrote no summary.json in {project}")
    return found[-1]


def timed_render(workspace: Path, project: Path, a: argparse.Namespace, log_path: Path,
                 extra: tuple[str, ...] = ()) -> dict:
    clean_project(workspace, project, extra=extra)
    run = run_sve(workspace, ["render", project.name, *(["--draft"] if a.draft else [])], log_path, a.quiet,
                  a.timeout)
    summary_path = latest_summary(project)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    video = summary_path.parent / summary.get("video", "")
    dur = summary.get("duration_sec")
    res = {k: run[k] for k in ("wall_sec", "cpu_user_sec", "cpu_sys_sec", "cpu_sec", "max_rss_mb") if k in run}
    res.update({
        "render_time_sec": summary.get("render_time_sec"), "duration_sec": dur,
        "sec_per_output_sec": round(run["wall_sec"] / dur, 4) if dur else None,
        "shots": summary.get("shots"), "resolution": summary.get("resolution"),
        "output_mb": round(video.stat().st_size / 1e6, 2) if video.is_file() else None,
        "version_dir": summary_path.parent.name, "log": str(log_path),
        **stages(run, project / "cache" / "segments"),
    })
    return res


def run_scenario(key: str, a: argparse.Namespace, root: Path) -> dict:
    """Media → project → prepare (untimed for the stats, recorded) → warmup → timed runs."""
    base = key.removesuffix("-draft")
    name = base.removesuffix("-quick")
    p = PARAMS[base]
    workspace = (root / key).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / MARKER).write_text("workspace created by scripts/bench.py; its renders are deleted between runs\n",
                                    encoding="utf-8")
    if name in EXTRA_SCENARIOS:
        setup = setup_analyze if name == "analyze" else setup_explainer
        project_name, media_sec = setup(workspace, a.media_dir, p, a)
    else:
        setup = setup_vertical if name == "vertical60" else setup_slideshow
        project_name, media_sec = setup(workspace, a.media_dir, p)
    project = workspace / "projects" / project_name
    logs = workspace / "logs"
    print(f"── {key}: preparing {project_name} …", flush=True)
    t0 = time.monotonic()
    planned, extra = None, {}
    if name == "analyze":
        pass                                              # every timed run analyzes from scratch
    elif name == "explainer75":
        run_sve(workspace, ["analyze", project_name], logs / "analyze.log", a.quiet, a.timeout)
        _, sentences = explainer_script(p["sentences"], p["graphics"])
        extra["script_words"] = write_script_words(project, sentences, p["presenter"]["dur"])
        run_sve(workspace, ["plan", project_name, "--force"], logs / "plan.log", a.quiet, a.timeout)
    elif name == "slideshow600":
        run_sve(workspace, ["plan", project_name, "--force"], logs / "plan.log", a.quiet, a.timeout)
    if name != "analyze":
        view = run_sve(workspace, ["view", project_name], logs / "view.log", a.quiet, a.timeout)  # analysis, music
        planned = next((m for _, line in view["lines"] if (m := VIEW_RE.search(line))), None)
    prepare_sec = round(time.monotonic() - t0, 3)
    print(f"   {key} prepared in {prepare_sec:.1f} s"
          + (f": {planned.group(1)} shots, {planned.group(2)} s planned" if planned else ""), flush=True)
    runs, warm = [], None
    for i in range(0 if a.prepare_only else int(a.warmup) + a.repeat):
        tag = "warmup" if a.warmup and i == 0 else f"run {len(runs) + 1}/{a.repeat}"
        if name == "analyze":
            print(f"── {key}: analyze ({tag}) …", flush=True)
            r = timed_analyze(workspace, project, a, logs / f"analyze_{i + 1}.log")
            print(f"   {key} {tag}: {r['wall_sec']:.1f} s wall, transcription {r.get('transcription_sec')} s, "
                  f"{r['words']} words ({', '.join(e['engine'] for e in r['transcription']) or 'no engine'})",
                  flush=True)
        else:
            print(f"── {key}: render ({tag}) …", flush=True)
            r = timed_render(workspace, project, a, logs / f"render_{i + 1}.log",
                             extra=("voice",) if name == "explainer75" else ())
            print(f"   {key} {tag}: {r['wall_sec']:.1f} s wall for {r['duration_sec']} s of video "
                  f"({r['sec_per_output_sec']} s/s)", flush=True)
        if tag == "warmup":
            warm = r
        else:
            runs.append(r)
    return {"project": project_name, "workspace": str(workspace), "params": p, "draft": a.draft,
            "media_sec": round(media_sec, 3), "prepare_sec": prepare_sec,
            "planned": planned and {"shots": int(planned.group(1)), "duration_sec": float(planned.group(2))},
            "settings": {"engine": a.engine, "analysis_jobs": a.analysis_jobs}, **extra,
            "warmup": warm,
            "runs": runs, "stats": summarize(runs)}


def results_table(res: dict) -> str:
    lines = ["| scenario | metric | median | min | max |", "|---|---|---:|---:|---:|"]
    for key, sc in res["scenarios"].items():
        if "error" in sc:
            lines.append(f"| {key} | error | {sc['error']} | | |")
            continue
        for m in ("media_sec", "prepare_sec"):            # once per scenario, not per run
            if isinstance(sc.get(m), (int, float)):
                lines.append(f"| {key} | {m} (once) | {_fmt(sc[m])} | | |")
        for m in COMPARE_METRICS:
            s = sc["stats"].get(m)
            if s:
                lines.append(f"| {key} | {m} | {_fmt(s['median'])} | {_fmt(s['min'])} | {_fmt(s['max'])} |")
    return "\n".join(lines)


def write_results(path: Path, res: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(res, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_results(path: Path) -> dict:
    try:
        res = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"cannot read results file {path}: {e}")
    if not isinstance(res, dict) or not isinstance(res.get("scenarios"), dict):
        raise SystemExit(f"{path} is not a bench results file (no \"scenarios\")")
    return res


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.compare:
        ra, rb = (load_results(p) for p in a.compare)
        print(compare_table(ra, rb, str(a.compare[0]), str(a.compare[1])))
        return 0
    if shutil.which("ffmpeg") is None:
        raise SystemExit("FFmpeg is required (https://ffmpeg.org)")
    sys.path.insert(0, str(Path(__file__).resolve().parent))      # _synth, also when imported as a module
    env = environment()
    stamp = datetime.now(timezone.utc)
    out = a.out or a.media_dir.parent / "results" / f"{stamp:%Y%m%d-%H%M%S}-{env['git_rev'] or 'norev'}.json"
    res = {"bench_version": BENCH_VERSION, "created": stamp.isoformat(timespec="seconds"),
           "args": {"scenario": a.scenario, "quick": a.quick, "repeat": a.repeat, "warmup": a.warmup,
                    "draft": a.draft, "timeout": a.timeout, "prepare_only": a.prepare_only,
                    "engine": a.engine, "analysis_jobs": a.analysis_jobs},
           "env": env, "scenarios": {}}
    print(f"bench: {env['ffmpeg']} · Python {env['python']} · {env['cpu_count']} CPUs · git {env['git_rev']}"
          f" · {', '.join(f'{k}={v}' for k, v in env['env'].items() if v is not None) or 'no SVE_* overrides'}")
    tmp = None if a.workdir else tempfile.TemporaryDirectory(prefix="sve_bench_")
    root = (a.workdir or Path(tmp.name)).resolve()
    failed = False
    try:
        for key in scenario_keys(a):
            try:
                res["scenarios"][key] = run_scenario(key, a, root)
            except (BenchError, subprocess.CalledProcessError) as e:
                failed = True
                res["scenarios"][key] = {"error": str(e)}
                print(f"!! {key}: {e}", flush=True)
            write_results(out, res)                  # partial results survive a later failure
    finally:
        if tmp is not None:
            tmp.cleanup()
    print()
    print(results_table(res))
    print(f"\nresults → {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
