"""AutoMix adapter: measures songs and renders a mix plan (domain/automix.py) to one WAV.

  - features(): integrated loudness (EBU R128, the same measurement as the mastering), a 0.1 s RMS
    envelope, onset rate and low-band accents on the beat grid (for the downbeat phase). Cached as
    JSON next to the mix, keyed by the file identity and the beat grid.
  - render(): one song in memory at a time (48 kHz stereo float32): loudness gain, true-peak-safe
    soft limiter, time-stretch of the outgoing tail (Rubber Band CLI with a pre-roll time map, so
    the tempo change is seamless; FFmpeg atempo when Rubber Band is missing), equal-power crossfade
    with a gliding high-pass "bass swap", streamed to a 24-bit WAV. Cached by a hash of the inputs
    and the plan. A short (not beat-matched) crossfade gets a smooth make-up gain where the sum
    falls below the quieter of the two songs around it (splice_makeup): it follows the K-weighted
    loudness curve measured on the rendered overlap (BS.1770 momentary loudness in the 1 s windows
    `sve check` uses), and the gained, limited overlap is measured again to fill what is left.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ...domain.automix import equal_power
from ...log import log
from ..system.tools import FFMPEG
from .loudness import ebur128

SR = 48000
FEATURES_VERSION = 1
RENDER_VERSION = 3              # 2: make-up gain in the short crossfades; 3: measured loudness curve
HOP = 0.1                       # RMS envelope step (s)
CEILING_DB = -1.5               # sample-peak ceiling of the mix (true peak stays ≈ under −1 dBTP)
PRE_ROLL = 0.5                  # seconds stretched at ratio 1 before the tail (seamless joint)
JOINT = 0.02                    # crossfade between the body and the stretched tail
FULL_HZ = 20.0                  # "no high-pass" end of the gliding filter
REF_SEC = 5.0                   # level of a song next to a transition: this many seconds of it …
REF_GAP = 1.0                   # … ending / starting this far from the overlap (as `sve check` measures)
MAKEUP_MAX_DB = 6.0             # largest make-up gain inside a short crossfade
MAKEUP_MARGIN_DB = 0.5          # aim this far under the quieter neighbour (never louder than both)
MAKEUP_SLEW_DB = 12.0           # dB per second: fastest make-up gain change (no audible pumping)
MAKEUP_PASSES = 4               # measure → gain passes (the later ones correct what smoothing / limiting left)
LOUD_HOP = 0.1                  # momentary loudness: 0.4 s windows every 0.1 s (EBU R128)
LOUD_WIN = 4                    # … in hops
DIP_WIN = 10                    # `sve check` dip window: 1 s of momentary values, in hops
# BS.1770 K-weighting at 48 kHz: high shelf (head) + RLB high-pass, as biquad sections
K_SOS = np.array([[1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585],
                  [1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621]])


def rubberband_path() -> str | None:
    return shutil.which("rubberband") or next((p for p in ("/opt/homebrew/bin/rubberband",
                                                           "/usr/local/bin/rubberband") if Path(p).exists()), None)


def _decode(path: Path, sr: int = SR, channels: int = 2) -> np.ndarray:
    """The whole file as float32 (samples, channels)."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path), "-vn",
                        "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"Could not decode {Path(path).name}: {r.stderr.decode(errors='replace')[-400:]}")
    return np.frombuffer(r.stdout, np.float32).reshape(-1, channels).copy()


def _identity(path: Path) -> dict:
    st = Path(path).stat()
    return {"path": str(Path(path).resolve()), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _key(data) -> str:
    return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ── features ─────────────────────────────────────────────────────────────────

def _stft_mag(y: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    n = 1 + max(len(y) - n_fft, 0) // hop
    win = np.hanning(n_fft).astype(np.float32)
    out = []
    for i in range(0, n, 2048):                      # in blocks: bounded memory on long songs
        idx = (np.arange(i, min(i + 2048, n))[:, None] * hop + np.arange(n_fft)[None, :])
        frames = y[np.minimum(idx, len(y) - 1)] * win
        out.append(np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32))
    return np.concatenate(out) if out else np.zeros((0, n_fft // 2 + 1), np.float32)


def compute_features(path: Path, beats: list[float]) -> dict:
    """{"duration", "lufs", "hop", "rms_db", "onset_rate", "accents", "peak_db"} of a song."""
    sr = 22050
    y = _decode(path, sr, 1)[:, 0]
    dur = len(y) / sr
    hop = int(sr * HOP)
    n = len(y) // hop
    rms = np.sqrt(np.mean(y[: n * hop].reshape(n, hop).astype(np.float64) ** 2, axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms)
    # onsets: positive spectral flux of the log magnitude
    n_fft, h = 1024, 512
    mag = _stft_mag(y, n_fft, h)
    fps = sr / h
    logm = np.log1p(100 * mag)
    flux = np.maximum(np.diff(logm, axis=0), 0)
    full = np.concatenate([[0.0], flux.sum(axis=1)])
    low_bins = max(int(200 * n_fft / sr), 2)
    low = np.concatenate([[0.0], flux[:, 1:low_bins].sum(axis=1)])
    onset_rate = 0.0
    if len(full) > 10:
        from scipy.ndimage import uniform_filter1d
        base = uniform_filter1d(full, size=max(int(0.5 * fps), 3))
        thr = base + 0.5 * full.std()
        peaks = (full[1:-1] > thr[1:-1]) & (full[1:-1] >= full[:-2]) & (full[1:-1] > full[2:])
        t_peaks = (np.flatnonzero(peaks) + 1) / fps
        kept, last = 0, -1.0
        for t in t_peaks:                            # at most one onset every 50 ms
            if t - last >= 0.05:
                kept, last = kept + 1, t
        audible = max(float((rms_db > -50).sum()) * HOP, 1.0)
        onset_rate = kept / audible
    w = max(int(0.05 * fps), 1)
    accents = []
    for b in beats:
        i = int(round(b * fps))
        lo, hi = max(i - w, 0), min(i + w + 1, len(low))
        accents.append(round(float(low[lo:hi].max()) if hi > lo else 0.0, 4))
    lufs = ebur128(path)["integrated"]
    return {"duration": round(dur, 4), "lufs": round(float(lufs), 2) if np.isfinite(lufs) else None,
            "hop": HOP, "rms_db": [round(float(v), 1) for v in rms_db], "onset_rate": round(onset_rate, 3),
            "accents": accents, "peak_db": round(float(20 * np.log10(np.abs(y).max() + 1e-12)), 2),
            "v": FEATURES_VERSION}


def features(path: Path, analysis: dict, cache_dir: Path) -> dict:
    beats = [round(float(b), 4) for b in analysis.get("beats") or []]
    key = _key({"id": _identity(path), "beats": beats, "v": FEATURES_VERSION})
    f = Path(cache_dir) / f"features_{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            pass
    data = compute_features(path, beats)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data))
    return data


# ── signal helpers ───────────────────────────────────────────────────────────

def soft_limit(x: np.ndarray, sr: int = SR, ceiling_db: float = CEILING_DB) -> np.ndarray:
    """Look-ahead peak limiter (gain = moving average of a moving minimum, so it never overshoots)."""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d

    ceiling = 10 ** (ceiling_db / 20)
    peak = np.abs(x).max(axis=1) if x.ndim > 1 else np.abs(x)
    if not len(peak) or peak.max() <= ceiling:
        return x
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-9)).astype(np.float32)
    w = max(int(0.04 * sr), 1)
    g = minimum_filter1d(need, size=2 * w + 1, mode="nearest")
    g = uniform_filter1d(g, size=2 * w + 1, mode="nearest")
    g = np.minimum(g, need)                           # float rounding: never above what is needed
    return x * (g[:, None] if x.ndim > 1 else g)


def glide_highpass(x: np.ndarray, sr: int, f_from: float, f_to: float, pos: np.ndarray) -> np.ndarray:
    """High-pass whose cutoff glides (log scale) from f_from (pos 0) to f_to (pos 1), per sample.

    A bank of zero-phase Butterworth high-passes blended by the position: no clicks, no phase
    smear between neighbours. FULL_HZ means unfiltered."""
    from scipy.signal import butter, sosfiltfilt

    if len(x) == 0 or max(f_from, f_to) <= FULL_HZ:
        return x
    hi = max(f_from, f_to)
    bank_f = np.geomspace(FULL_HZ, hi, 8)
    sigs = [x] + [sosfiltfilt(butter(2, f, "highpass", fs=sr, output="sos"), x, axis=0).astype(np.float32)
                  for f in bank_f[1:]]
    a, b = np.log(max(f_from, FULL_HZ)), np.log(max(f_to, FULL_HZ))
    lc = a + (b - a) * np.clip(pos, 0, 1)
    lf = np.log(bank_f)
    j = np.clip(np.searchsorted(lf, lc) - 1, 0, len(lf) - 2)
    frac = np.clip((lc - lf[j]) / (lf[j + 1] - lf[j]), 0, 1).astype(np.float32)
    y = np.zeros_like(x)
    for k in range(len(lf)):
        wk = np.where(j == k, 1 - frac, 0) + np.where(j + 1 == k, frac, 0)
        if wk.any():
            y += sigs[k] * wk[:, None].astype(np.float32)
    return y


def _slice(x: np.ndarray, a: int, b: int) -> np.ndarray:
    """x[a:b] padded with silence outside the file."""
    out = np.zeros((max(b - a, 0), x.shape[1]), np.float32)
    s0, s1 = max(a, 0), min(b, len(x))
    if s1 > s0:
        out[s0 - a: s1 - a] = x[s0:s1]
    return out


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros((n - len(x), x.shape[1]), np.float32)])


def stretch(x: np.ndarray, pre: int, n_out: int, work: Path) -> np.ndarray:
    """x = [pre-roll (kept at ratio 1) | tail] → pre-roll + the tail stretched to n_out samples."""
    n_in = len(x) - pre
    if n_in <= 0:
        return _fit(x, pre + n_out)
    if abs(n_out / n_in - 1) < 1e-5:
        return _fit(x, pre + n_out)
    import soundfile as sf

    src, dst, tmap = work / "rb_in.wav", work / "rb_out.wav", work / "rb_map.txt"
    sf.write(src, x, SR, subtype="FLOAT")
    rb = rubberband_path()
    if rb:
        tmap.write_text(f"0 0\n{pre} {pre}\n{len(x)} {pre + n_out}\n")
        cmd = [rb, "-3", "-q", "--centre-focus", "-t", f"{(pre + n_out) / len(x):.9f}", "-M", str(tmap),
               str(src), str(dst)]
    else:                                             # no Rubber Band: FFmpeg atempo on the whole piece
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
               "-af", f"atempo={len(x) / (pre + n_out):.6f}", "-c:a", "pcm_f32le", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"time-stretch failed: {r.stderr[-400:]}")
    y, _ = sf.read(dst, dtype="float32", always_2d=True)
    return _fit(y, pre + n_out)


def _k_energy(x: np.ndarray) -> np.ndarray:
    """Per-sample K-weighted energy summed over the channels (BS.1770, all channel weights 1)."""
    from scipy.signal import sosfilt

    y = sosfilt(K_SOS, np.asarray(x, np.float64), axis=0)
    return (y ** 2).sum(axis=1) if y.ndim > 1 else y ** 2


def level_db(x: np.ndarray) -> float | None:
    """Loudness (LUFS: K-weighted, ungated) of a stereo piece; None if empty or silent."""
    if not len(x):
        return None
    e = float(np.mean(_k_energy(x)))
    return -0.691 + 10 * np.log10(e) if e > 1e-10 else None


def momentary(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Momentary loudness (LUFS, 0.4 s windows) every LOUD_HOP s: value k = window ending at (k+1)·hop."""
    hop = int(LOUD_HOP * sr)
    k = len(x) // hop
    if k == 0:
        return np.zeros(0)
    e = _k_energy(x[: k * hop]).reshape(k, hop).mean(axis=1)
    c = np.concatenate([[0.0], np.cumsum(e)])
    lo = np.maximum(np.arange(1, k + 1) - LOUD_WIN, 0)
    m = (c[1:] - c[lo]) / (np.arange(1, k + 1) - lo)
    return -0.691 + 10 * np.log10(np.maximum(m, 1e-12))


def dip_levels(mom: np.ndarray) -> np.ndarray:
    """Level of every 1 s window (energy mean of DIP_WIN momentary values), one per hop: what
    `sve check` compares with the music around a splice. Window j = momentary values j … j + 9."""
    k = len(mom) - DIP_WIN + 1
    if k <= 0:
        return np.full(1, 10 * np.log10(np.mean(10 ** (mom / 10)))) if len(mom) else np.zeros(0)
    c = np.concatenate([[0.0], np.cumsum(10 ** (mom / 10))])
    return 10 * np.log10(np.maximum((c[DIP_WIN:] - c[:k]) / DIP_WIN, 1e-12))


def _gain_step(err: np.ndarray, k: int) -> np.ndarray:
    """dB to add per audio hop (k hops) from the error of every 1 s window (err[j] > 0: too quiet;
    window j covers the hops j − LOUD_WIN + 1 … j + DIP_WIN − 1, centred on j + 3): each error is
    put at its window's centre, held to the ends and smoothed over 0.7 s."""
    from scipy.ndimage import uniform_filter1d

    c = (DIP_WIN - LOUD_WIN) // 2 + 1
    hops = np.arange(len(err)) + c
    keep = hops < k
    if not keep.any():
        return np.zeros(k)
    step = np.interp(np.arange(k), hops[keep], err[keep])
    return uniform_filter1d(step, size=min(7, k), mode="nearest")


def _shape(db: np.ndarray) -> np.ndarray:
    """Make-up dB per hop clipped to [0, MAKEUP_MAX_DB], 0 dB at both ends, at most MAKEUP_SLEW_DB/s."""
    g = np.clip(db, 0.0, MAKEUP_MAX_DB)
    g[0] = g[-1] = 0.0
    step = MAKEUP_SLEW_DB * LOUD_HOP
    for i in range(1, len(g)):                     # slew limit, forward then backward
        g[i] = min(g[i], g[i - 1] + step)
    for i in range(len(g) - 2, -1, -1):
        g[i] = min(g[i], g[i + 1] + step)
    return g


def splice_makeup(mix: np.ndarray, ref_out_db: float | None, ref_in_db: float | None,
                  sr: int = SR, before: np.ndarray | None = None, after: np.ndarray | None = None) -> np.ndarray:
    """Per-sample gain (≥ 1) for a crossfade `mix` whose loudness dips below the quieter of the two
    songs around it (ref_*: their loudness (level_db) just before / after the transition).

    The K-weighted momentary loudness of the mix is measured in the 1 s windows `sve check` uses
    (with `before` / `after`: the audio right around it, so the windows at the joints see what the
    listener hears). A window under the target (quieter neighbour − MAKEUP_MARGIN_DB) asks for the
    missing dB at its centre, one over the quieter neighbour gives some back; the curve is smoothed,
    held at 0 dB at both ends, kept in [0, MAKEUP_MAX_DB] and changes by at most MAKEUP_SLEW_DB per
    second, so it moves slowly and the joints stay seamless. The gained mix goes through the same
    soft limiter as the render and is measured again, up to MAKEUP_PASSES times."""
    n = len(mix)
    refs = [r for r in (ref_out_db, ref_in_db) if r is not None]
    if n < int(0.2 * sr) or not refs:
        return np.ones(n, np.float32)
    target = min(refs) - MAKEUP_MARGIN_DB
    hop = int(LOUD_HOP * sr)
    empty = np.zeros((0, mix.shape[1]), np.float32)
    pre = empty if before is None else before
    pre = pre[len(pre) - (len(pre) // hop) * hop:]                # whole hops: the grid stays aligned
    post = empty if after is None else after
    c0, k = len(pre) // hop, -(-n // hop)
    t = (np.arange(k) + 0.5) * hop
    db = np.zeros(k)
    gain = np.ones(n, np.float32)
    for _ in range(MAKEUP_PASSES):
        y = soft_limit(mix * gain[:, None], sr)
        lv = dip_levels(momentary(np.concatenate([pre, y, post]), sr))[c0: c0 + max(k - DIP_WIN, 0) + 1]
        err = np.where(lv < target, target - lv, np.minimum(target + MAKEUP_MARGIN_DB - lv, 0.0))
        if not len(err) or (err.max() < 0.1 and (err.min() > -0.1 or not db.any())):
            break
        db = _shape(db + _gain_step(err, k))
        if not db.any():
            break
        g_db = np.interp(np.arange(n), np.concatenate([[0], t, [n - 1]]), np.concatenate([[0.0], db, [0.0]]))
        gain = (10 ** (g_db / 20)).astype(np.float32)
    return gain


# ── rendering ────────────────────────────────────────────────────────────────

def _samples(t: float) -> int:
    return int(round(float(t) * SR))


def render(paths: list[Path], plan: dict, out: Path) -> Path:
    """Renders the mix plan to `out` (24-bit stereo WAV, 48 kHz)."""
    import soundfile as sf

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.wav")
    trs = plan["transitions"]
    carry: np.ndarray | None = None
    carry_ref: float | None = None              # loudness of the outgoing song before its tail
    carry_ctx: np.ndarray | None = None         # … the second before `pending` (measured, already written)
    pending: np.ndarray | None = None           # its last REF_GAP s, written with the short crossfade
    gap, ref = _samples(REF_GAP), _samples(REF_SEC)
    written = 0
    with tempfile.TemporaryDirectory(prefix="automix_") as wd, \
            sf.SoundFile(tmp, "w", SR, 2, subtype="PCM_24") as w:
        work = Path(wd)
        for i, (path, e) in enumerate(zip(paths, plan["songs"])):
            log(f"  AutoMix: {Path(path).name} ({e['gain_db']:+.1f} dB)")
            x = _decode(Path(path))
            if e["gain_db"]:
                x *= np.float32(10 ** (e["gain_db"] / 20))
            x = soft_limit(x)
            a = _samples(e["in"])
            if e.get("stretch_from") is not None:
                s0, s1 = _samples(e["stretch_from"]), _samples(e["out"])
                pre = min(_samples(PRE_ROLL), max(s0 - a, 0))
                n_tail = _samples(e["tail"])
                st = stretch(_slice(x, s0 - pre, s1), pre, n_tail, work)
                track = _slice(x, a, s0)
                j = min(_samples(JOINT), pre, len(track))
                if j > 0:                             # seamless joint into the stretched tail
                    ramp = np.linspace(0, 1, j, dtype=np.float32)[:, None]
                    track[-j:] = track[-j:] * (1 - ramp) + st[pre - j:pre] * ramp
                track = np.concatenate([track, st[pre:]])
            else:
                track, n_tail = _slice(x, a, _samples(e["out"])), 0
            del x
            if carry is not None:                     # incoming head: high-passed, fading in
                n = len(carry)
                head = _fit(track[:n], n)
                track = track[n:]
                pos = (np.arange(n, dtype=np.float32) + 0.5) / max(n, 1)
                _, g_in = equal_power(pos)
                cutoff = float(trs[i - 1]["cutoff_hz"])
                head = glide_highpass(head, SR, cutoff, FULL_HZ, pos) * g_in[:, None].astype(np.float32)
                mixed = carry + head
                if pending is not None:               # short crossfade: no dip below the songs around it
                    nxt = track[gap: gap + ref] if len(track) > gap + ref // 2 else track[:ref]
                    g2 = min(gap, max(len(track) - n_tail, 0))         # the make-up gain may end up to
                    mixed = np.concatenate([pending, mixed, track[:g2]])  # REF_GAP s outside the overlap
                    track = track[g2:]
                    mixed *= splice_makeup(mixed, carry_ref, level_db(nxt), SR, carry_ctx, track[:gap])[:, None]
                w.write(soft_limit(mixed))
                written += len(mixed)
                carry = pending = None
            if n_tail:
                body, tail = track[:-n_tail] if n_tail < len(track) else track[:0], _fit(track[-n_tail:], n_tail)
                carry_ref = level_db(body[-gap - ref: -gap] if len(body) > gap + ref // 2 else body[-ref:])
                if not trs[i]["matched"]:             # short crossfade: its make-up gain may start
                    g1 = min(gap, len(body))          # REF_GAP s before the overlap
                    pending = body[len(body) - g1:].copy()
                    carry_ctx = body[max(len(body) - g1 - gap, 0): len(body) - g1].copy()
                    body = body[: len(body) - g1]
                pos = (np.arange(n_tail, dtype=np.float32) + 0.5) / n_tail
                g_out, _ = equal_power(pos)
                lows_out = np.clip((pos - 0.5) / 0.5, 0, 1)          # loses its lows in the second half
                carry = glide_highpass(tail, SR, FULL_HZ, float(trs[i]["cutoff_hz"]), lows_out) \
                    * g_out[:, None].astype(np.float32)
            else:
                body = track
                fo = _samples(e.get("fade_out") or 0)
                if fo > 0 and len(body):
                    fo = min(fo, len(body))
                    body[-fo:] *= np.cos(np.linspace(0, np.pi / 2, fo, dtype=np.float32))[:, None]
            w.write(body)
            written += len(body)
    tmp.replace(out)
    log(f"  AutoMix: {written / SR:.1f} s → {out.name}")
    return out


def render_cached(paths: list[Path], plan: dict, cache_dir: Path) -> Path:
    """The mix of `paths` with `plan`, rendered once (cache/automix/mix_<hash>.wav)."""
    key = _key({"songs": [_identity(p) for p in paths], "plan": plan, "v": RENDER_VERSION,
                "rubberband": bool(rubberband_path())})
    out = Path(cache_dir) / f"mix_{key}.wav"
    if out.exists():
        return out
    return render(paths, plan, out)
