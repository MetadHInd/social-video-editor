"""MusicSynthesizer with numpy: a score (domain/compose.py) → a stereo 24-bit FLAC (or WAV).

Signal path: every event is drawn by its voice (adapters/synth/voices.py, cached per sound, note and
length) at its velocity and pan, into three buses — dry, "pumped" (pad and arpeggio, which duck
under every kick like a sidechain) and a mono reverb send (a seeded noise impulse response, stereo).
Then: high-pass 30 Hz, a −2 dB bell at 2.5 kHz (room for the voice), the score's gates (where the
music sounds; cut dead / faded), loudness to `lufs` (ITU-R BS.1770 integrated, gated) and a
look-ahead limiter to `ceiling_db`. The samples are quantized to 24 bits and hashed (SHA-256 of the
little-endian int32 values), so the same score gives the same file and the same hash.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from ...domain.models import UserError

SR = 48000
DEFAULTS = {"sample_rate": SR, "lufs": -16.0, "ceiling_db": -2.0, "voice_dip_db": 2.0, "reverb": 1.0,
            "mix": {}}
ROLE_DB = {"drums": 0.0, "bass": -2.0, "pad": -7.0, "arp": -8.0, "lead": -6.0, "fx": -8.0}   # LU from the drums
INST_DB = {"kick": 0.0, "snare": 3.0, "clap": 2.0, "rim": 1.0, "hat": 6.0, "ohat": 2.0, "shaker": 5.0, "tom": -2.0,
           "crash": 0.0, "impact": 0.0, "riser": -5.0}     # inside the drums / effects
SENDS = {"drums": 0.05, "bass": 0.0, "pad": 0.25, "arp": 0.3, "lead": 0.35, "fx": 0.2}
PUMPED = ("pad", "arp")
PAN_SPREAD = {"pad": 0.0, "arp": 1.0, "lead": 1.0, "drums": 1.0, "fx": 0.0, "bass": 0.0}


class NumpySynthesizer:
    """Renders composed scores. Imports numpy / scipy / soundfile only when it renders."""

    def render(self, score: dict, out: Path, settings: dict | None = None) -> dict:
        """score → audio file at `out` (.flac or .wav, 24 bit) → {"sha256", "lufs", "peak_db",
        "duration", "sample_rate", "file"}."""
        import numpy as np

        s = {**DEFAULTS, **(settings or {})}
        x = self.samples(score, s)
        q = np.clip(np.round(x * 8388607.0), -8388607, 8388607).astype("<i4")
        digest = hashlib.sha256(q.tobytes()).hexdigest()
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        import soundfile as sf

        fmt = "WAV" if out.suffix.lower() == ".wav" else "FLAC"
        sf.write(str(out), (q << 8).astype("<i4"), int(s["sample_rate"]), subtype="PCM_24", format=fmt)
        lufs, peak = loudness(x, int(s["sample_rate"])), float(np.max(np.abs(x))) if len(x) else 0.0
        return {"file": out.name, "sha256": digest, "lufs": round(lufs, 2),
                "peak_db": round(20 * math.log10(peak), 2) if peak > 0 else -120.0,
                "duration": round(len(x) / int(s["sample_rate"]), 4), "sample_rate": int(s["sample_rate"]),
                "channels": 2, "bits": 24}

    def samples(self, score: dict, settings: dict | None = None):
        """The mastered stereo samples (float64, shape (n, 2)) of a score."""
        import numpy as np

        from . import voices

        s = {**DEFAULTS, **(settings or {})}
        sr = int(s["sample_rate"])
        n = int(round(float(score["duration"]) * sr))
        cache: dict = {}
        pad_cfg = s.get("pad") or {}
        seed = int(score.get("seed", 0))
        placed = []                                        # (role, start sample, samples × velocity gain, pan)
        kicks = []
        for idx, e in enumerate(score["events"]):
            role, inst = e["r"], e["i"]
            if inst == "kick":
                kicks.append(float(e["t"]))
            sound = _voice(voices, cache, sr, e, idx, seed, pad_cfg)
            i0 = int(round(float(e["t"]) * sr))
            if sound is None or i0 >= n:
                continue
            if i0 < 0:
                sound, i0 = sound[-i0:], 0
            gain = 10 ** (INST_DB.get(inst, 0.0) / 20) * float(e["v"]) ** 1.3
            placed.append((role, i0, sound[: n - i0], gain, float(e.get("p", 0.0)) * PAN_SPREAD.get(role, 1.0)))
        gains = _balance(np, placed, n, sr, {**ROLE_DB, **(s.get("mix") or {})}) if s.get("balance", True) else \
            {r: 10 ** (float(v) / 20) for r, v in {**ROLE_DB, **(s.get("mix") or {})}.items()}
        dry, pumped, send = np.zeros((n, 2)), np.zeros((n, 2)), np.zeros(n)
        for role, i0, sound, gain, pan in placed:
            seg = sound * (gain * gains.get(role, 1.0))
            left, right = math.cos((pan + 1) * math.pi / 4), math.sin((pan + 1) * math.pi / 4)
            bus = pumped if role in PUMPED else dry
            bus[i0:i0 + len(seg), 0] += seg * left * math.sqrt(2)
            bus[i0:i0 + len(seg), 1] += seg * right * math.sqrt(2)
            if SENDS.get(role):
                send[i0:i0 + len(seg)] += seg * SENDS[role]
        if kicks:
            pumped *= _pump(np, kicks, n, sr)[:, None]
        mixbus = dry + pumped
        if float(s.get("reverb", 1.0)) > 0 and np.any(send):
            mixbus += _reverb(np, send, sr, seed) * float(s.get("reverb", 1.0))
        mixbus = _master_eq(mixbus, sr, float(s.get("voice_dip_db", 2.0)))
        mixbus *= _gates(np, score.get("gates") or [[0.0, float(score["duration"]), 0.0, 0.0]], n, sr)[:, None]
        if s.get("lufs") is None:                          # raw (measuring the parts of a mix)
            return mixbus
        return _level(np, mixbus, sr, float(s["lufs"]), float(s["ceiling_db"]))

    def pack_presets(self, pack: str) -> dict:
        """The `music:` block of packs/<pack>/pack.yaml ({} without one)."""
        from ..files.jsonio import read_yaml
        from ..files.packs import FilePackRepository

        data = read_yaml(FilePackRepository().folder(pack) / "pack.yaml") or {}
        music = data.get("music") or {}
        if not isinstance(music, dict):
            raise UserError(f"Pack '{pack}': music must be a mapping (music: {{preset: …, presets: {{…}}}})")
        return music

    def encode(self, src: Path, out: Path) -> Path:
        """A compressed copy for listening (.m4a AAC 192 kb/s, .mp3 or .ogg) with FFmpeg."""
        from ..ffmpeg.runner import run

        codec = {".m4a": ["-c:a", "aac", "-b:a", "192k"], ".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
                 ".ogg": ["-c:a", "libvorbis", "-q:a", "6"]}.get(Path(out).suffix.lower())
        if codec is None:
            raise UserError(f"Unknown audio format {Path(out).suffix} (use .flac, .wav, .m4a, .mp3 or .ogg)")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-i", src, *codec, out])
        return Path(out)


def _voice(voices, cache: dict, sr: int, e: dict, idx: int, seed: int, pad_cfg: dict):
    inst = e["i"]
    length = round(float(e["d"]), 3)
    if inst in voices.DRUMS:
        variant = (idx * 7 + seed) % 4
        key = (inst, variant, length if inst in ("crash", "impact", "riser") else 0)
        if key not in cache:
            cache[key] = voices.DRUMS[inst](sr, variant=variant, length=length)
        return cache[key]
    if inst in voices.PADS:
        notes = tuple(e.get("c") or [e.get("n", 60)])
        key = (inst, notes, length)
        if key not in cache:
            cache[key] = voices.pad(sr, notes, length, sound=inst, cutoff=float(pad_cfg.get("cutoff", 1800)))
        return cache[key]
    if inst in voices.PITCHED:
        key = (inst, e.get("n"), length, e.get("g"))
        if key not in cache:
            cache[key] = voices.PITCHED[inst](sr, e.get("n", 60), length, glide=e.get("g"))
        return cache[key]
    return None


def _balance(np, placed, n: int, sr: int, targets: dict) -> dict:
    """Gain of every role so that, where it plays, it sits `targets[role]` LU from the drums
    (measured: each role is laid out alone and its integrated loudness taken)."""
    measured = {}
    for role in {p[0] for p in placed}:
        buf = np.zeros(n, dtype=np.float32)
        for r, i0, sound, gain, _ in placed:
            if r == role:
                buf[i0:i0 + len(sound)] += (sound * gain).astype(np.float32)
        measured[role] = loudness(buf, sr)
    ref = measured.get("drums")
    if ref is None or not math.isfinite(ref):
        ref = max((v for v in measured.values() if math.isfinite(v)), default=-20.0)
    return {role: 10 ** ((ref + float(targets.get(role, 0.0)) - lv) / 20) if math.isfinite(lv) else 1.0
            for role, lv in measured.items()}


def _pump(np, kicks: list[float], n: int, sr: int, depth: float = 0.45, tau: float = 0.09):
    g = np.ones(n)
    L = int(0.3 * sr)
    curve = 1 - depth * np.exp(-np.arange(L) / sr / tau)
    for t in kicks:
        i = int(round(t * sr))
        if i >= n:
            continue
        m = min(L, n - i)
        g[i:i + m] = np.minimum(g[i:i + m], curve[:m])
    return g


def _reverb(np, send, sr: int, seed: int, seconds: float = 1.8, rt60: float = 1.5):
    """Stereo reverb of a mono send: two seeded noise impulse responses, darkened, 20 ms pre-delay."""
    from scipy.signal import oaconvolve

    from .voices import filt

    t = np.arange(int(seconds * sr)) / sr
    out = []
    for ch in range(2):
        rng = np.random.default_rng([seed & 0xFFFF, 4242, ch])
        ir = rng.standard_normal(len(t)) * np.exp(-6.91 * t / rt60)
        ir = filt(filt(ir, "lowpass", 5000, sr), "highpass", 220, sr)
        ir = np.concatenate([np.zeros(int(0.02 * sr)), ir])
        ir /= np.sqrt(np.sum(ir ** 2)) + 1e-12
        out.append(oaconvolve(send, ir)[: len(send)])
    return np.stack(out, axis=1) * 0.5


def _biquad_peak(f0: float, gain_db: float, q: float, sr: int):
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / sr
    alpha = math.sin(w0) / (2 * q)
    b = [1 + alpha * A, -2 * math.cos(w0), 1 - alpha * A]
    a = [1 + alpha / A, -2 * math.cos(w0), 1 - alpha / A]
    return [x / a[0] for x in b], [x / a[0] for x in a]


def _master_eq(x, sr: int, dip_db: float):
    from scipy.signal import lfilter

    from .voices import filt

    y = filt(x, "highpass", 30, sr) if x.ndim == 1 else _per_channel(x, lambda c: filt(c, "highpass", 30, sr))
    if dip_db:
        b, a = _biquad_peak(2500.0, -abs(dip_db), 1.0, sr)
        y = _per_channel(y, lambda c: lfilter(b, a, c))
    return y


def _per_channel(x, fn):
    import numpy as np

    return np.stack([fn(x[:, c]) for c in range(x.shape[1])], axis=1)


def _gates(np, gates: list, n: int, sr: int):
    env = np.zeros(n)
    for start, end, fade_in, fade_out in gates:
        a, b = int(round(start * sr)), min(int(round(end * sr)), n)
        if b <= a:
            continue
        seg = np.ones(b - a)
        fi, fo = int(fade_in * sr), int(fade_out * sr)
        if fi > 0:
            seg[:fi] = np.linspace(0, 1, min(fi, len(seg)))[: len(seg[:fi])]
        if fo > 0:
            k = min(fo, len(seg))
            seg[-k:] *= np.linspace(1, 0, k)
        env[a:b] = np.maximum(env[a:b], seg)
    return env


def _level(np, x, sr: int, target: float, ceiling_db: float):
    """Gain to the target loudness, then a look-ahead limiter (twice: the limiter lowers it a little)."""
    if not np.any(x):
        return x
    ceiling = 10 ** (ceiling_db / 20)
    y = x
    gain = 0.0
    for _ in range(3):
        L = loudness(y, sr)
        if not math.isfinite(L):
            break
        gain += target - L
        y = _limit(np, x * 10 ** (gain / 20), sr, ceiling)
        if abs(loudness(y, sr) - target) < 0.1:
            break
    return np.clip(y, -ceiling, ceiling)


def _limit(np, x, sr: int, ceiling: float, look: float = 0.005):
    from scipy.ndimage import minimum_filter1d, uniform_filter1d

    peak = np.max(np.abs(x), axis=1)
    if peak.max() <= ceiling:
        return x
    w = max(int(look * sr) | 1, 3)
    g = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    g = minimum_filter1d(g, w, mode="nearest")
    g = uniform_filter1d(g, w, mode="nearest")
    g = minimum_filter1d(g, 3, mode="nearest")
    return x * g[:, None]


# ── loudness (ITU-R BS.1770-4, integrated, gated) ────────────────────────────

def _k_filters(sr: int):
    """The K-weighting pre-filter (high shelf + high-pass) for any sample rate."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a2 = [1.0, 2 * (K * K - 1) / (1 + K / Q + K * K), (1 - K / Q + K * K) / (1 + K / Q + K * K)]
    b2 = [1.0, -2.0, 1.0]
    return (b1, a1), (b2, a2)


def loudness(x, sr: int) -> float:
    """Integrated loudness (LUFS) of mono (n,) or stereo (n, 2) float samples; −inf for silence."""
    import numpy as np
    from scipy.signal import lfilter

    x = np.asarray(x, float)
    if x.ndim == 1:
        x = x[:, None]
    (b1, a1), (b2, a2) = _k_filters(sr)
    y = lfilter(b2, a2, lfilter(b1, a1, x, axis=0), axis=0)
    block, hop = int(0.4 * sr), int(0.1 * sr)
    if len(y) < block:
        return float("-inf")
    p = np.cumsum(np.concatenate([np.zeros((1, y.shape[1])), y ** 2]), axis=0)
    starts = np.arange(0, len(y) - block + 1, hop)
    z = (p[starts + block] - p[starts]) / block
    power = z.sum(axis=1)
    lk = -0.691 + 10 * np.log10(np.maximum(power, 1e-20))
    keep = lk > -70
    if not keep.any():
        return float("-inf")
    rel = -0.691 + 10 * np.log10(power[keep].mean()) - 10
    keep &= lk > rel
    if not keep.any():
        return float("-inf")
    return float(-0.691 + 10 * np.log10(power[keep].mean()))
