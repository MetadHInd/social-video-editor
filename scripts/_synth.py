"""Synthetic media and workspace helpers shared by the scripts (make_demo.py, bench.py).

Everything here is generated on the fly — nothing is downloaded and no real footage, photo or song
is used:

  * music: synthesized click / kick / chord-pad loops (numpy) written as 16-bit WAV;
  * demo clips: FFmpeg `gradients` backgrounds with a moving Pillow-drawn shape (H.264);
  * phone-like clips: FFmpeg `testsrc2` + temporal noise, HEVC 10-bit with an AAC track, like the
    footage an iPhone records (used by the benchmark: the decoder and the encoder get real work);
  * photos: Pillow drawings (gradient sky, sun, hills, a "Photo N" label) with EXIF capture dates.

The engine itself is always run through its CLI (`sve()` / `sve_command()`), as a user would.
"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[1]
SR = 44100


# ── helpers ──────────────────────────────────────────────────────────────────

def ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def sve_env(workspace: Path) -> dict:
    """Environment for a CLI run against `workspace`: the repository's src/ first on PYTHONPATH (works
    without `pip install -e .`), and Hugging Face offline so an optional model never downloads."""
    return {**os.environ, "SVE_WORKSPACE": str(workspace), "HF_HUB_OFFLINE": "1",
            "PYTHONPATH": os.pathsep.join(x for x in (str(REPO / "src"), os.environ.get("PYTHONPATH")) if x)}


def sve_command(*args: str) -> list[str]:
    """The argv of `sve <args>` with the current interpreter (no reliance on the `sve` entry point)."""
    return [sys.executable, "-m", "social_video_editor", *map(str, args)]


def sve(workspace: Path, *args: str) -> None:
    """Run the CLI exactly as a user would, against the demo workspace."""
    print(f"$ sve {' '.join(args)}", flush=True)
    subprocess.run(sve_command(*args), env=sve_env(workspace), check=True)


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
        # the bar's samples as a slice (same samples as the mask t >= bar_t & t < bar_t + 4 beats,
        # without scanning the whole track once per bar: long tracks stay fast)
        i0, i1 = np.searchsorted(t, bar_t), np.searchsorted(t, bar_t + 4 * beat)
        seg = slice(i0, i1)
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


# ── synthetic vertical clips (demo) ──────────────────────────────────────────

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


# ── synthetic phone clips (benchmark) ────────────────────────────────────────

def make_phone_clips(folder: Path, count: int, W: int = 2160, H: int = 3840, dur: float = 6.0,
                     rates: tuple[int, ...] = (30, 60)) -> list[Path]:
    """`count` vertical HEVC 10-bit clips with an AAC track, like an iPhone records (hvc1 tag, .mov).

    Frame rates cycle through `rates` (default: half at 30 fps, half at 60 fps). The picture is
    `testsrc2` (moving pattern, hue shifted per clip) with light temporal noise, so both the decoder
    and the engine's encoder get real work instead of nearly static frames; the audio is a sine tone
    over quiet pink noise. `-preset ultrafast` keeps generation affordable at 4K.
    """
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(count):
        fps = rates[i % len(rates)]
        clip = folder / f"IMG_{i + 1:04d}.mov"
        ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={fps}:d={dur},hue=h={i * 47 % 360},"
                                    "noise=alls=6:allf=t",
               "-f", "lavfi", "-i", f"sine=f={220 + 55 * i}:sample_rate=48000:d={dur}",
               "-f", "lavfi", "-i", f"anoisesrc=c=pink:a=0.02:r=48000:d={dur}:seed={i + 1}",
               "-filter_complex", "[1:a][2:a]amix=inputs=2:normalize=0,aformat=channel_layouts=stereo[a]",
               "-map", "0:v", "-map", "[a]", "-t", dur,
               "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast",
               "-x265-params", "log-level=error", "-tag:v", "hvc1",
               "-c:a", "aac", "-b:a", "160k", clip)
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
    # one column of the vertical gradient, repeated across the width (the full-size float array
    # would be ~300 MB for a 12 MP photo)
    column = (np.array(top) * (1 - yy) + np.array(bottom) * yy).astype("uint8")
    sky = np.ascontiguousarray(np.broadcast_to(column, (H, W, 3)))
    im = Image.fromarray(sky, "RGB")
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


def make_photos(folder: Path, count: int = 12, landscape: tuple[int, int] = (1600, 1066),
                portrait: tuple[int, int] = (1080, 1440), minutes_apart: int = 37) -> list[Path]:
    """`count` photos taken over one synthetic day; file names are shuffled so the engine has to sort
    them by their EXIF capture time. Every 4th photo is portrait (blurred-background framing),
    photo 5 is underexposed (auto-tone)."""
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    names = [f"IMG_{k:04d}.jpg" for k in rng.sample(range(1000, 9999), count)]
    start = datetime(2024, 1, 1, 9, 0, 0)
    out = []
    for n in range(1, count + 1):
        size = portrait if n % 4 == 0 else landscape
        taken = start + timedelta(minutes=minutes_apart * n + rng.randint(0, 20))
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


def latest_video(project: Path) -> Path:
    videos = sorted(project.glob("v*/*.mp4"), key=lambda p: p.stat().st_mtime)
    if not videos:
        raise SystemExit(f"no render found in {project}")
    return videos[-1]
