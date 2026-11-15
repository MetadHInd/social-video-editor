"""The sounds of the composer: every instrument synthesized from sines and filtered noise (numpy,
scipy filters). No samples. Each voice returns a mono float64 array at peak ≈ 1.

Pitched voices are additive (sums of sine partials) so their spectrum is chosen, not filtered: the
partials between ~1.2 and ~3.8 kHz (where a voice is understood) are 2.5 dB lower, nothing above 14 kHz.
Noise comes from a seeded generator (the same score always gives the same samples).
"""
from __future__ import annotations

import math
import zlib

import numpy as np

# amplitude weight of a partial by frequency: room for the voice in 1–4 kHz, soft top end
_WEIGHT_F = np.log2([20.0, 800.0, 1300.0, 3800.0, 5200.0, 9000.0, 14000.0, 24000.0])
_WEIGHT_A = np.array([1.0, 1.0, 0.75, 0.75, 0.8, 0.45, 0.0, 0.0])


def weight(f: float) -> float:
    return float(np.interp(math.log2(max(f, 20.0)), _WEIGHT_F, _WEIGHT_A))


def midi_hz(n: float) -> float:
    return 440.0 * 2 ** ((float(n) - 69) / 12)


def noise(sr: int, seconds: float, key: str, variant: int = 0) -> np.ndarray:
    rng = np.random.default_rng([zlib.crc32(key.encode()), int(variant)])
    return rng.standard_normal(max(int(seconds * sr), 1))


def _t(sr: int, seconds: float) -> np.ndarray:
    return np.arange(max(int(seconds * sr), 1)) / sr


def _sos(kind: str, f, sr: int, order: int = 2):
    from scipy.signal import butter

    return butter(order, f, kind, fs=sr, output="sos")


def filt(x: np.ndarray, kind: str, f, sr: int, order: int = 2) -> np.ndarray:
    from scipy.signal import sosfilt

    return sosfilt(_sos(kind, f, sr, order), x)


def _norm(x: np.ndarray) -> np.ndarray:
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return x / m if m > 0 else x


def _envelope(n: int, sr: int, attack: float, hold: float, release: float) -> np.ndarray:
    """Linear attack, hold to `hold` seconds, then a cosine release."""
    t = np.arange(n) / sr
    env = np.clip(t / max(attack, 1e-4), 0, 1)
    rel = np.clip((t - hold) / max(release, 1e-4), 0, 1)
    return env * (0.5 + 0.5 * np.cos(np.pi * rel))


def additive(sr: int, freq: float, length: float, partials, attack: float, release: float,
             phase_key: str = "", vibrato: float = 0.0, detune: float = 0.0) -> np.ndarray:
    """Sum of sine partials [(ratio, amplitude, decay seconds or 0 = sustained)], weighted by `weight`."""
    total = length + release
    t = _t(sr, total)
    x = np.zeros_like(t)
    rng = np.random.default_rng(zlib.crc32(f"{phase_key}:{freq:.3f}".encode()))
    vib = 1.0 + (vibrato * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.25) / 0.3, 0, 1) if vibrato else 0.0)
    for ratio, amp, decay in partials:
        f = freq * ratio * (1 + detune)
        w = amp * weight(f)
        if w <= 1e-4 or f >= sr / 2 - 500:
            continue
        ph = 2 * np.pi * f * (np.cumsum(vib) / sr if vibrato else t) + rng.uniform(0, 2 * np.pi)
        part = np.sin(ph) * w
        if decay:
            part *= np.exp(-t / decay)
        x += part
    return x * _envelope(len(t), sr, attack, length, release)


def saw_partials(freq: float, cutoff: float, slope: float = 1.0, limit: float = 6000.0) -> list:
    out = []
    k = 1
    while freq * k < limit and k <= 40:
        f = freq * k
        out.append((k, (1.0 / k ** slope) / (1 + (f / cutoff) ** 4), 0.0))
        k += 1
    return out


# ── pitched sounds ───────────────────────────────────────────────────────────

def pluck(sr, note, length, **_):
    f = midi_hz(note)
    parts = [(k, 1.0 / k, 0.32 / k ** 0.6) for k in range(1, 16)]
    return _norm(additive(sr, f, length, parts, 0.002, 0.06, "pluck"))


def bell(sr, note, length, **_):
    f = midi_hz(note)
    parts = [(1, 1.0, 1.5), (2, 0.38, 0.7), (3, 0.12, 0.45), (4.07, 0.08, 0.25), (5.43, 0.04, 0.15)]
    x = additive(sr, f, max(length, 0.9), parts, 0.003, 0.25, "bell")
    return _norm(x)


def keys(sr, note, length, **_):
    f = midi_hz(note)
    scale = (261.6 / f) ** 0.35
    parts = [(1, 1.0, 2.2 * scale), (2, 0.5, 1.1 * scale), (3, 0.28, 0.6 * scale), (4, 0.16, 0.4 * scale),
             (5, 0.09, 0.3 * scale), (6, 0.06, 0.2 * scale), (7, 0.03, 0.15 * scale), (8, 0.02, 0.12 * scale)]
    a = additive(sr, f, length, parts, 0.004, 0.35, "keys")
    b = additive(sr, f, length, parts[:3], 0.004, 0.35, "keys2", detune=0.0018)
    return _norm(a + 0.35 * b[:len(a)])


def soft(sr, note, length, **_):
    """A soft, flute-like lead: three partials, slow attack, a little vibrato."""
    f = midi_hz(note)
    x = additive(sr, f, length, [(1, 1.0, 0), (2, 0.3, 0), (3, 0.14, 0), (4, 0.06, 0), (5, 0.03, 0)], 0.06, 0.15,
                 "soft", vibrato=0.003)
    return _norm(x)


def string(sr, note, length, **_):
    """A plucked string (nylon-like): partials shaped by the pluck position, higher ones fade first."""
    f = midi_hz(note)
    pos = 0.18
    parts = [(k, abs(math.sin(math.pi * k * pos)) / k, 1.6 / k ** 0.8) for k in range(1, 14)]
    return _norm(additive(sr, f, max(length, 0.6), parts, 0.0015, 0.2, "string"))


def pad(sr, notes, length, sound="warm", cutoff=1800.0, **_):
    """A chord held for `length`: three slightly detuned saw-like voices per note."""
    attack, slope = {"warm": (0.12, 1.0), "soft": (0.25, 1.4), "dark": (0.4, 1.2)}.get(sound, (0.12, 1.0))
    out = None
    for n in notes:
        f = midi_hz(n)
        parts = saw_partials(f, cutoff, slope)
        for j, det in enumerate((-0.0035, 0.0, 0.0042)):
            x = additive(sr, f, length, parts, attack, 0.35, f"pad{j}", detune=det)
            out = x if out is None else out + x[:len(out)]
    if sound == "dark" and notes:
        sub = additive(sr, midi_hz(min(notes) - 12), length, [(1, 0.6, 0)], attack, 0.35, "padsub")
        out = out + sub[:len(out)]
    return _norm(out)


def bass_808(sr, note, length, glide=None, **_):
    t = _t(sr, length + 0.03)
    f0 = midi_hz(note)
    f = np.full_like(t, f0)
    if glide is not None:
        f = f0 + (midi_hz(glide) - f0) * np.exp(-t / 0.05)
    x = np.sin(2 * np.pi * np.cumsum(f) / sr)
    env = np.minimum(1, t / 0.004) * np.exp(-t / 0.9) * np.clip((length + 0.03 - t) / 0.03, 0, 1)
    x = x * env
    mid = filt(np.tanh(5 * x), "bandpass", [160, 900], sr)       # heard on a phone speaker
    return _norm(x + 0.5 * mid)


def bass_synth(sr, note, length, **_):
    f = midi_hz(note)
    parts = [(k, 1.0 / k / (1 + (f * k / 520) ** 4), (0.6 if k == 1 else 0.22 / k ** 0.4)) for k in range(1, 10)]
    return _norm(additive(sr, f, length, parts, 0.004, 0.05, "bass"))


def bass_round(sr, note, length, **_):
    f = midi_hz(note)
    return _norm(additive(sr, f, length, [(1, 1.0, 1.6), (2, 0.3, 0.8), (3, 0.08, 0.4)], 0.012, 0.12, "round"))


# ── drums and effects ────────────────────────────────────────────────────────

def kick(sr, variant=0, **_):
    t = _t(sr, 0.45)
    f = 46 + 95 * np.exp(-t / 0.028)
    x = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / 0.3)
    click = filt(noise(sr, 0.004, "kick", variant), "lowpass", 3000, sr)
    x[:len(click)] += 0.4 * click * np.linspace(1, 0, len(click))
    return _norm(np.tanh(1.6 * x))


def snare(sr, variant=0, **_):
    t = _t(sr, 0.3)
    body = 0.7 * np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.05)
    n = filt(noise(sr, 0.3, "snare", variant), "bandpass", [1500, 7500], sr) * np.exp(-t / 0.1)
    return _norm(body + 0.8 * _norm(n))


def clap(sr, variant=0, **_):
    t = _t(sr, 0.35)
    env = np.zeros_like(t)
    for k, o in enumerate((0.0, 0.011, 0.022)):
        env += (t >= o) * np.exp(-np.clip(t - o, 0, None) / (0.008 if k < 2 else 0.15))
    x = filt(noise(sr, 0.35, "clap", variant), "bandpass", [1000, 6000], sr) * env
    x += 0.5 * np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.06)
    return _norm(x)


def rim(sr, variant=0, **_):
    t = _t(sr, 0.08)
    x = np.sin(2 * np.pi * 820 * t) * np.exp(-t / 0.012) + 0.4 * np.sin(2 * np.pi * 1630 * t) * np.exp(-t / 0.008)
    x += 0.3 * filt(noise(sr, 0.08, "rim", variant), "bandpass", [2000, 6000], sr) * np.exp(-t / 0.006)
    return _norm(x)


def hat(sr, variant=0, open_=False, **_):
    t = _t(sr, 0.35 if open_ else 0.07)
    x = filt(noise(sr, len(t) / sr, "ohat" if open_ else "hat", variant), "highpass", 7000, sr, 4)
    x = filt(x, "lowpass", 13000, sr)
    return _norm(x * np.exp(-t / (0.17 if open_ else 0.022)))


def shaker(sr, variant=0, **_):
    t = _t(sr, 0.12)
    x = filt(noise(sr, 0.12, "shaker", variant), "bandpass", [4000, 10000], sr)
    return _norm(x * np.minimum(1, t / 0.012) * np.exp(-t / 0.045))


def tom(sr, variant=0, **_):
    t = _t(sr, 0.45)
    f = 88 + 55 * np.exp(-t / 0.06)
    x = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / 0.22)
    x += 0.1 * filt(noise(sr, 0.45, "tom", variant), "lowpass", 1500, sr) * np.exp(-t / 0.03)
    return _norm(x)


def crash(sr, variant=0, length=2.2, **_):
    t = _t(sr, length)
    x = filt(noise(sr, length, "crash", variant), "highpass", 3500, sr)
    x = filt(x, "lowpass", 11000, sr)
    rng = np.random.default_rng(variant + 11)
    for f in (3150, 4870, 6210, 7730):
        x += 0.1 * np.sin(2 * np.pi * f * t + rng.uniform(0, 6))
    return _norm(x * np.exp(-t / 0.75) * np.minimum(1, t / 0.002))


def impact(sr, variant=0, length=1.0, **_):
    """The hit at a section's entry: a sub drop and a low thump."""
    t = _t(sr, max(length, 0.6))
    f = 72 * np.exp(-t / 0.45) + 28
    x = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / 0.55)
    x += 0.35 * filt(noise(sr, len(t) / sr, "impact", variant), "lowpass", 400, sr) * np.exp(-t / 0.12)
    mid = filt(np.tanh(3 * x), "bandpass", [120, 700], sr)
    return _norm(x + 0.3 * mid)


def riser(sr, variant=0, length=2.0, **_):
    """Filtered noise rising into the next section (the last bar before a chorus)."""
    t = _t(sr, length)
    n = noise(sr, length, "riser", variant)
    x = np.zeros_like(t)
    bands = ((300, 800), (600, 1500), (1200, 3000), (2500, 6000), (5000, 12000))
    for j, band in enumerate(bands):
        centre = (j + 1) / (len(bands) + 1)
        env = np.exp(-((t / length - centre) / 0.28) ** 2) * (t / length) ** 1.5
        x += filt(n, "bandpass", list(band), sr) * env
    return _norm(x * np.clip((length - t) / 0.05, 0, 1))


PITCHED = {"pluck": pluck, "bell": bell, "keys": keys, "soft": soft, "string": string,
           "808": bass_808, "synth": bass_synth, "round": bass_round}
PADS = ("warm", "soft", "dark")
DRUMS = {"kick": kick, "snare": snare, "clap": clap, "rim": rim, "hat": hat,
         "ohat": lambda sr, variant=0, **k: hat(sr, variant, open_=True), "shaker": shaker, "tom": tom,
         "crash": crash, "impact": impact, "riser": riser}
SOUNDS = (*PITCHED, *PADS, *DRUMS)
