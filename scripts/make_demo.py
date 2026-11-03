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
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[1]
SR = 44100
MAX_GIF_BYTES = 3 * 1024 * 1024


# ── helpers ──────────────────────────────────────────────────────────────────

def ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def sve(workspace: Path, *args: str) -> None:
    """Run the CLI exactly as a user would, against the demo workspace."""
    env = {**os.environ, "SVE_WORKSPACE": str(workspace), "HF_HUB_OFFLINE": "1",
           "PYTHONPATH": os.pathsep.join(x for x in (str(REPO / "src"), os.environ.get("PYTHONPATH")) if x)}
    print(f"$ sve {' '.join(args)}", flush=True)
    subprocess.run([sys.executable, "-m", "social_video_editor", *args], env=env, check=True)


def font(size: int) -> ImageFont.ImageFont:
    for name in ("Inter-Bold.otf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                      # Pillow < 10.1
        return ImageFont.load_default()


# ── synthetic music ──────────────────────────────────────────────────────────

def synth_track(path: Path, bpm: float, bars: int, root_hz: float, seed: int, lead: float = 0.5,
                outro_bars: int = 2) -> Path:
    """A 4/4 loop: kick on beats 1 and 3, hat on every 8th, a 4-chord pad (one chord per bar),
    a short plucked arpeggio, `outro_bars` of fade-out. 16-bit stereo WAV."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    body = bars * 4 * beat
    n = int((lead + body + 1.0) * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)

    def hit(at: float, sig: np.ndarray, gain: float) -> None:
        i = int(at * SR)
        m = min(len(sig), n - i)
        if m > 0:
            y[i:i + m] += gain * sig[:m]

    kt = np.arange(int(0.25 * SR)) / SR
    kick = np.sin(2 * np.pi * (45 + 80 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 14)
    ht = np.arange(int(0.05 * SR)) / SR
    hat = rng.standard_normal(len(ht)) * np.exp(-ht * 90)
    hat = np.diff(hat, prepend=0.0)                          # brighter: crude high-pass
    pt = np.arange(int(0.35 * SR)) / SR
    progression = [0, 5, 3, 4]                               # scale degrees of the bar roots
    major = [0, 2, 4, 5, 7, 9, 11]

    def degree_hz(d: int, octave: int = 0) -> float:
        semis = major[d % 7] + 12 * (d // 7 + octave)
        return root_hz * 2 ** (semis / 12)

    for b in range(bars):
        bar_t = lead + b * 4 * beat
        deg = progression[b % 4]
        chord = [degree_hz(deg), degree_hz(deg + 2), degree_hz(deg + 4)]
        seg = (t >= bar_t) & (t < bar_t + 4 * beat)
        local = t[seg] - bar_t
        env = np.minimum(1, local / 0.05) * np.minimum(1, (4 * beat - local) / 0.1)
        y[seg] += 0.06 * env * sum(np.sin(2 * np.pi * f * t[seg]) for f in chord)
        for k in range(4):
            if k % 2 == 0:
                hit(bar_t + k * beat, kick, 0.9)
            for half in (0, 0.5):
                hit(bar_t + (k + half) * beat, hat, 0.12 if half else 0.18)
            f = degree_hz(deg + (0, 2, 4, 7)[k], octave=1)
            hit(bar_t + k * beat + beat / 2, np.sin(2 * np.pi * f * pt) * np.exp(-pt * 9), 0.22)
    fade_start = lead + (bars - outro_bars) * 4 * beat
    fade = t >= fade_start
    y[fade] *= np.clip(1 - (t[fade] - fade_start) / (outro_bars * 4 * beat), 0, 1) ** 2
    y[t < lead] = 0
    y = 0.7 * y / max(1e-9, np.abs(y).max())
    pcm = (np.stack([y, y], axis=1) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


# ── synthetic vertical clips ─────────────────────────────────────────────────

CLIPS = [  # (gradient colors, shape)
    (("0x2b1055", "0x7597de", "0xff6fb5"), "circle"),
    (("0x0f3443", "0x34e89e", "0xf9f871"), "square"),
    (("0x41295a", "0xff8a5b", "0xffd86b"), "triangle"),
    (("0x1d2b64", "0x5ee7df", "0xb490ca"), "ring"),
]


def shape_png(path: Path, kind: str, size: int = 360) -> Path:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m, fill = size // 10, (255, 255, 255, 235)
    if kind == "circle":
        d.ellipse([m, m, size - m, size - m], fill=fill)
    elif kind == "square":
        d.rounded_rectangle([m, m, size - m, size - m], radius=size // 8, fill=fill)
    elif kind == "triangle":
        d.polygon([(size / 2, m), (size - m, size - m), (m, size - m)], fill=fill)
    else:
        d.ellipse([m, m, size - m, size - m], outline=fill, width=size // 9)
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shadow.putalpha(im.getchannel("A").filter(ImageFilter.GaussianBlur(size // 25)).point(lambda a: a // 3))
    out = Image.alpha_composite(shadow, im)
    out.save(path)
    return path


def make_clips(folder: Path, W: int = 720, H: int = 1280, dur: float = 6.0) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (colors, kind) in enumerate(CLIPS):
        png = shape_png(folder / f"_shape_{i}.png", kind)
        c = ":".join(f"c{k}={col}" for k, col in enumerate(colors))
        # the shape drifts across the frame and bobs; the background gradient slowly rotates
        x = f"(W-w)/2+{W // 4}*sin(t*{0.7 + 0.2 * i:.2f})"
        y = f"(H-h)/2+{H // 5}*cos(t*{0.9 + 0.15 * i:.2f})"
        clip = folder / f"clip_{i + 1}.mp4"
        ffmpeg("-f", "lavfi", "-i", f"gradients=s={W}x{H}:{c}:nb_colors=3:speed=0.03:d={dur}:r=30:seed={i + 1}",
               "-loop", "1", "-i", png,
               "-filter_complex", f"[1:v]rotate=a=t*{0.6 + 0.3 * i:.2f}:c=none:ow=iw:oh=ih[s];"
                                  f"[0:v][s]overlay=x='{x}':y='{y}':shortest=1,format=yuv420p",
               "-t", dur, "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", clip)
        png.unlink()
        out.append(clip)
    return out


# ── synthetic photos ─────────────────────────────────────────────────────────

PALETTES = [  # (sky top, sky bottom, sun, hills)
    ((255, 170, 120), (255, 226, 170), (255, 245, 210), (120, 90, 140)),
    ((90, 160, 230), (190, 225, 250), (255, 250, 220), (70, 140, 90)),
    ((40, 50, 110), (230, 120, 110), (255, 210, 120), (50, 40, 80)),
    ((120, 200, 220), (240, 245, 235), (255, 255, 240), (90, 160, 140)),
    ((250, 130, 160), (255, 210, 180), (255, 240, 200), (150, 80, 120)),
    ((60, 90, 160), (150, 190, 230), (240, 240, 255), (40, 80, 110)),
]


def draw_photo(path: Path, n: int, size: tuple[int, int], taken: datetime, rng: random.Random,
               dark: bool = False) -> Path:
    W, H = size
    top, bottom, sun, hills = PALETTES[n % len(PALETTES)]
    yy = np.linspace(0, 1, H)[:, None, None]
    sky = (np.array(top) * (1 - yy) + np.array(bottom) * yy) * np.ones((1, W, 1))
    im = Image.fromarray(sky.astype("uint8"), "RGB")
    d = ImageDraw.Draw(im)
    r = min(W, H) // rng.randint(7, 10)
    cx, cy = rng.randint(r, W - r), rng.randint(int(H * 0.15), int(H * 0.4))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=sun)
    for layer in range(3):                                   # three rolling hill layers
        base = H * (0.62 + 0.11 * layer)
        amp, freq, phase = H * rng.uniform(0.03, 0.08), rng.uniform(1.0, 2.5), rng.uniform(0, 6.3)
        pts = [(x, base - amp * math.sin(freq * x / W * 2 * math.pi + phase)) for x in range(0, W + 1, 8)]
        shade = tuple(int(c * (1 - 0.18 * layer)) for c in hills)
        d.polygon([(0, H), *pts, (W, H)], fill=shade)
    label = f"Photo {n}"
    f = font(max(24, min(W, H) // 9))
    box = d.textbbox((0, 0), label, font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    px, py = (W - tw) // 2, int(H * 0.86) - th
    d.rounded_rectangle([px - th // 2, py - th // 3, px + tw + th // 2, py + th + th // 2],
                        radius=th // 2, fill=(255, 255, 255))
    d.text((px - box[0], py - box[1]), label, font=f, fill=(40, 40, 40))
    if dark:                                                 # underexposed: auto_tone lifts it
        im = Image.eval(im, lambda v: int(v * 0.35))
    exif = Image.Exif()
    stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
    exif[306] = stamp                                        # DateTime
    exif.get_ifd(0x8769)[36867] = stamp                      # DateTimeOriginal
    im.save(path, "JPEG", quality=90, exif=exif.tobytes())
    return path


def make_photos(folder: Path, count: int = 12) -> list[Path]:
    """`count` photos taken over one synthetic day; file names are shuffled so the engine has to sort
    them by their EXIF capture time. Every 4th photo is portrait (blurred-background framing),
    photo 5 is underexposed (auto-tone)."""
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    names = [f"IMG_{k:04d}.jpg" for k in rng.sample(range(1000, 9999), count)]
    start = datetime(2024, 1, 1, 9, 0, 0)
    out = []
    for n in range(1, count + 1):
        size = (1080, 1440) if n % 4 == 0 else (1600, 1066)
        taken = start + timedelta(minutes=37 * n + rng.randint(0, 20))
        out.append(draw_photo(folder / names[n - 1], n, size, taken, rng, dark=(n == 5)))
    return out


# ── projects ─────────────────────────────────────────────────────────────────

def write_project(workspace: Path, name: str, recipe: str, sources: list[str], settings: dict,
                  plan: dict | None = None) -> Path:
    import yaml

    d = workspace / "projects" / name
    d.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "recipe": recipe, "sources": [{"path": s} for s in sources], "settings": settings}
    (d / "project.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if plan is not None:
        (d / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return d


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


def latest_video(project: Path) -> Path:
    videos = sorted(project.glob("v*/*.mp4"), key=lambda p: p.stat().st_mtime)
    if not videos:
        raise SystemExit(f"no render found in {project}")
    return videos[-1]


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
