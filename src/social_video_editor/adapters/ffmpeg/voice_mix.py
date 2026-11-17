"""Music measured against the voice (music.relative_to_voice).

Before the final mix, the voice of the joined video is measured from the rendered segments (their
own audio, placed on the output timeline with the same overlaps as the join): its integrated loudness
(EBU R128, per segment, energy-weighted) and where it speaks (voice_levels.speech_mask on 10 ms
levels). The music is cut / looped / faded as in the fixed mix, measured, and written once as a "bed"
whose level follows the voice (the numbers measured go to <version>/music_levels.json):
music.under_voice_db under it while it speaks, music.under_pause_db in
the pauses, music.intro_db louder before the first word (domain/voice_levels.py). The final mix then
takes that bed as it is (filters.final_mix with music._bed), so the old fixed chain is untouched for
everything that does not opt in.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ...domain import voice_levels as vl
from ...log import log
from . import filters, voice
from .loudness import ebur128
from .runner import run

BED_NAME = "_music_bed.flac"
LEVELS_FILE = "music_levels.json"   # what was measured, kept in the version folder (sve check / report)
QUIET_LUFS = -60.0          # a voice quieter than this is no voice: the fixed mix is used


def energy_mean(values: list[tuple[float, float]]) -> float:
    """Energy mean (LUFS) of (loudness, weight seconds) pairs; −inf without any."""
    num = sum(w * 10 ** (v / 10) for v, w in values if math.isfinite(v) and w > 0)
    den = sum(w for v, w in values if math.isfinite(v) and w > 0)
    return 10 * math.log10(num / den) if den and num > 0 else float("-inf")


def voice_track(segments: list[dict], files: list[Path], total: float) -> tuple:
    """(levels per FRAME on the output timeline, integrated loudness of the voice)."""
    import numpy as np

    n = max(int(math.ceil(total / vl.FRAME)), 1)
    db = np.full(n, -120.0)
    pos, parts = 0.0, []
    for i, (s, f) in enumerate(zip(segments, files)):
        dur, ov = float(s["dur"]), float(s.get("overlap") or 0)
        if i and filters.xfade_name(s.get("transition", "cut")) is not None and ov > 0:
            pos -= ov
        lv = voice.frame_levels(f, duration=dur)
        a = int(round(pos / vl.FRAME))
        b = min(a + len(lv), n)
        if b > a:
            db[a:b] = 10 * np.log10(10 ** (db[a:b] / 10) + 10 ** (lv[:b - a] / 10))
        loud = ebur128(f)["integrated"]
        if loud > QUIET_LUFS:
            parts.append((loud, dur))
        pos += dur
    return db, energy_mean(parts)


def raw_chain(m: dict, total: float, music_idx: int = 0) -> str:
    """The music cut, looped and faded exactly as the fixed mix does, without its volume."""
    loop = "" if filters.music_covers(m, total) else "aloop=loop=-1:size=2147483647,"
    return (f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=start={float(m.get('_start', 0)):.3f},asetpts=PTS-STARTPTS,"
            f"{loop}atrim=0:{total:.4f},"
            f"afade=t=in:d={float(m.get('fade_in', 0.25)):.2f},"
            f"afade=t=out:st={max(total - float(m['fade_out']), 0):.3f}:d={float(m['fade_out']):.3f}")


def music_bed(segments: list[dict], files: list[Path], music: Path, cfg: dict, total: float,
              workdir: Path) -> tuple[Path, dict] | None:
    """The music bed whose level follows the voice → (bed file, cfg with music._bed and the measured
    levels in music._levels), or None (no voice: the fixed mix is used)."""
    m = cfg["music"]
    s = vl.music_settings(m)
    db, voice_lufs = voice_track(segments, files, total)
    speech = vl.intervals(vl.speech_mask(db))
    if not speech or not math.isfinite(voice_lufs):
        log("Music: no voice measured; the fixed music level is used")
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / "_music_raw.flac"
    bed = workdir / BED_NAME
    env = workdir / "_music_env.f32"
    try:
        run(["ffmpeg", "-i", music, "-filter_complex", raw_chain(m, total) + "[m]", "-map", "[m]",
             "-t", f"{total:.4f}", "-c:a", "flac", "-ar", "48000", raw])
        music_lufs = ebur128(raw)["integrated"]
        if not math.isfinite(music_lufs) or music_lufs < -70:
            return None
        base = vl.music_base_gain(voice_lufs, music_lufs, s)
        curve = vl.music_curve(speech, total, s)
        voice.write_envelope(curve + base, env)
        run(["ffmpeg", "-i", raw, *voice.envelope_input(env), "-filter_complex",
             voice.multiply("[0:a]", "[1:a]", "bed"), "-map", "[bed]", "-t", f"{total:.4f}",
             "-c:a", "flac", "-ar", "48000", bed])
    finally:
        raw.unlink(missing_ok=True)
        env.unlink(missing_ok=True)
    levels = {"voice_lufs": round(voice_lufs, 1), "music_lufs": round(music_lufs, 1), "gain_db": round(base, 1),
              "under_voice_db": s["under_voice_db"], "under_pause_db": s["under_pause_db"],
              "intro_db": s["intro_db"], "speech_spans": len(speech)}
    (workdir / LEVELS_FILE).write_text(json.dumps(levels, indent=1), encoding="utf-8")
    log(f"Music: measured against the voice ({levels['voice_lufs']} LUFS; music {levels['music_lufs']} LUFS → "
        f"{base:+.1f} dB): {s['under_voice_db']:g} dB under it while it speaks, {s['under_pause_db']:g} dB in pauses"
        + (f", {s['intro_db']:+g} dB before the first word" if s["intro_db"] else ""))
    return bed, {**cfg, "music": {**m, "_bed": True, "_levels": levels}}
