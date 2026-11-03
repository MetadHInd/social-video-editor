#!/usr/bin/env python3
"""Build the README demo GIFs from 100 % synthetic material.

Everything the engine edits here is generated on the fly — nothing is downloaded and no real
footage, photo or song is used:

  * vertical clips: FFmpeg `gradients` backgrounds with a moving Pillow-drawn shape on top;
  * photos: Pillow drawings (gradient sky, sun, hills, a "Photo N" label) with EXIF capture dates,
    written under shuffled file names so the chronological ordering is visible;
  * music: synthesized click / kick / chord-pad loops (numpy), two of them form the playlist.

Two projects are built in a throw-away workspace and rendered with the regular CLI
(`python -m social_video_editor …`):

  1. `vertical_demo` — a short 9:16 edit from a hand-written plan.json: hook card, zoom punch,
     beat pulse, polaroid, collage, transitions, every cut on the beat;
  2. `slideshow_demo` — the `slideshow` recipe on a dozen photos with a 2-track AutoMix playlist,
     photo changes fitted to the beats of the mix (landscape 16:9).

Short excerpts of both renders are converted to small optimized GIFs (palettegen / paletteuse)
in docs/media/. GIFs are the only media committed to the repository.

Usage:
    python scripts/make_demo.py                     # temporary workspace, GIFs → docs/media/
    python scripts/make_demo.py --workdir /tmp/sve  # keep the workspace (renders, plans, caches)

Requires FFmpeg 7+ and the engine's dependencies (`pip install -e .`). Renders use `--draft`
(half resolution), which is plenty for 480 px GIFs. Takes a few minutes.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

# helpers shared with scripts/bench.py (the script's folder is on sys.path when it is run directly)
from _synth import REPO, ffmpeg, latest_video, make_clips, make_photos, sve, synth_track, write_project

MAX_GIF_BYTES = 3 * 1024 * 1024


# ── projects ─────────────────────────────────────────────────────────────────

def vertical_project(workspace: Path) -> Path:
    clips = make_clips(workspace / "sources" / "clips")
    synth_track(workspace / "music" / "vertical" / "loop_120.wav", bpm=120, bars=12, root_hz=220.0, seed=1)
    plan = {
        "shots": [
            {"source": 0, "start": 0.5, "end": 3.0, "role": "hook", "beats": 4, "transition": "cut",
             "effects": [{"type": "zoom_punch", "t": 0}]},
            {"source": 1, "start": 0.5, "end": 3.0, "beats": 4, "transition": "whip",
             "effects": [{"type": "beat_pulse"}]},
            {"type": "polaroid", "from_shot": 1, "beats": 3, "text": "Photo 1", "transition": "cut"},
            {"source": 2, "start": 0.3, "end": 4.5, "speed": 1.5, "beats": 4, "transition": "slide_up"},
            {"type": "collage", "from_shots": [0, 1, 3], "background_from": 3, "beats": 6, "transition": "zoom"},
            {"source": 3, "start": 0.5, "end": 3.5, "role": "ending", "beats": 4, "transition": "circle"},
        ],
        "texts": [
            {"text": "Synthetic demo", "shot": 0, "style": "hook"},
            {"text": "Beat-synced cuts", "description": "every cut lands on the beat", "shot": 1, "style": "card"},
            {"text": "Edited from a plan.json", "shot": 5, "style": "cta"},
        ],
    }
    settings = {"language": "en", "subtitles": {"auto": False},
                "music": {"file": "music/vertical/loop_120.wav", "start_at": 0.0},
                "output": {"fade_out": 0.5}}
    return write_project(workspace, "vertical_demo", "base", [str(c) for c in clips], settings, plan)


def slideshow_project(workspace: Path) -> Path:
    album = workspace / "sources" / "album"
    make_photos(album)
    # two tracks with close tempos (within ±8 %): AutoMix beat-matches the transition
    synth_track(workspace / "music" / "demo_set" / "01_track_a.wav", bpm=120, bars=16, root_hz=196.0, seed=2)
    synth_track(workspace / "music" / "demo_set" / "02_track_b.wav", bpm=124, bars=18, root_hz=233.1, seed=3)
    settings = {"language": "en", "music": {"playlist": ["demo_set"], "automix": {"enabled": True}}}
    return write_project(workspace, "slideshow_demo", "slideshow", [str(album)], settings)


# ── GIFs ─────────────────────────────────────────────────────────────────────

def to_gif(video: Path, out: Path, start: float, dur: float, width: int, fps: int = 12,
           speed: float = 1.0, colors: int = 128) -> int:
    """Two-pass palette GIF; steps fps / colors / width down until it fits MAX_GIF_BYTES."""
    out.parent.mkdir(parents=True, exist_ok=True)
    palette = out.with_suffix(".palette.png")
    attempts = [(fps, colors, width), (10, colors, width), (10, 96, width), (8, 96, width),
                (8, 64, int(width * 0.85)), (6, 64, int(width * 0.75))]
    try:
        for f, c, w in attempts:
            base = f"setpts=PTS/{speed},fps={f},scale={w}:-2:flags=lanczos"
            ffmpeg("-ss", start, "-t", dur * speed, "-i", video, "-vf",
                   f"{base},palettegen=max_colors={c}:stats_mode=diff", palette)
            ffmpeg("-ss", start, "-t", dur * speed, "-i", video, "-i", palette, "-lavfi",
                   f"{base}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
                   "-loop", "0", out)
            size = out.stat().st_size
            if size <= MAX_GIF_BYTES:
                print(f"  {out.name}: {size / 1024:.0f} KB ({w}px, {f} fps, {c} colors)")
                return size
        raise SystemExit(f"{out.name} is still larger than {MAX_GIF_BYTES} bytes")
    finally:
        palette.unlink(missing_ok=True)


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workdir", type=Path, help="workspace to build in (default: a temporary folder, deleted)")
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "media", help="where the GIFs go")
    ap.add_argument("--skip-render", action="store_true", help="reuse the renders already in --workdir")
    a = ap.parse_args(argv)
    if shutil.which("ffmpeg") is None:
        raise SystemExit("FFmpeg is required (https://ffmpeg.org)")

    tmp = None if a.workdir else tempfile.TemporaryDirectory(prefix="sve_demo_")
    workspace = (a.workdir or Path(tmp.name)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        if not a.skip_render:
            print("── vertical demo (9:16) ──")
            vertical_project(workspace)
            sve(workspace, "render", "vertical_demo", "--draft")
            print("── slideshow demo (16:9) ──")
            slideshow_project(workspace)
            sve(workspace, "plan", "slideshow_demo")
            sve(workspace, "render", "slideshow_demo", "--draft")
        vertical = latest_video(workspace / "projects" / "vertical_demo")
        slides = latest_video(workspace / "projects" / "slideshow_demo")
        print("── GIFs ──")
        to_gif(vertical, a.out / "demo_vertical.gif", start=0, dur=12.5, width=270, fps=12)
        to_gif(slides, a.out / "demo_slideshow.gif", start=0, dur=12, width=480, fps=12, speed=2.0)
    finally:
        if tmp is not None:
            tmp.cleanup()


if __name__ == "__main__":
    main()
