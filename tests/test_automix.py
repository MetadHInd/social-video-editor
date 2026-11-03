"""AutoMix: planning logic (pure) and the rendered continuous mix of a synthetic playlist."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from conftest import requires_ffmpeg
from social_video_editor.domain import automix
from social_video_editor.domain.check import silences

SR = 44100


# ── synthetic songs ──────────────────────────────────────────────────────────

def click_song(path: Path, bpm: float, body: float = 40.0, lead: float = 1.5, tail: float = 6.0,
               amp: float = 0.5, quiet_intro: float = 0.0, pad_hz: float = 220.0) -> Path:
    """Click track: low kick on every downbeat, high click on every beat, a soft pad under it,
    `lead` s of silence, an optional quiet intro (pad only), and a `tail` s fade-out.
    (Songs of one playlist get different pad pitches: identical sustained tones would cancel.)"""
    import soundfile as sf

    n = int((lead + quiet_intro + body + tail + 1.0) * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    T = 60.0 / bpm
    start = lead + quiet_intro
    k = 0
    while start + k * T < start + body + tail:
        i = int((start + k * T) * SR)
        m = min(int(0.12 * SR), n - i)
        tt = np.arange(m) / SR
        if k % 4 == 0:
            y[i:i + m] += 0.9 * np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 25)
        y[i:i + m] += 0.35 * np.sin(2 * np.pi * 1800 * tt) * np.exp(-tt * 60)
        k += 1
    y += 0.08 * np.sin(2 * np.pi * pad_hz * t) + 0.04 * np.sin(2 * np.pi * 1.5 * pad_hz * t)
    env = np.ones(n)
    env[t < lead] = 0
    env[(t >= lead) & (t < start)] = 0.1                      # quiet intro: the pad only, -20 dB
    fade = (t >= start + body) & (t < start + body + tail)
    env[fade] = np.linspace(1, 0.02, fade.sum()) ** 2
    env[t >= start + body + tail] = 0
    y = amp * y * env
    if quiet_intro:
        y[(t >= lead) & (t < start)] = amp * 0.1 * (0.08 * np.sin(2 * np.pi * pad_hz * t[(t >= lead) & (t < start)]))
    sf.write(path, np.stack([y, y], 1), SR)
    return path


def fake_song(name: str, bpm: float, dur: float = 120.0, first_beat: float = 0.5, phase: int = 0,
              outro: float | None = None, loud_from: float = 0.5, onset_rate: float = 3.0,
              body_db: float = -20.0) -> dict:
    """A planner song without audio (regular grid, given level profile)."""
    T = 60.0 / bpm
    beats = list(np.arange(first_beat, dur, T))
    return {"name": name, "duration": dur, "tempo": bpm, "interval": T, "beats": beats, "phase": phase,
            "lufs": -12.0, "onset_rate": onset_rate,
            "profile": {"start": 0.5, "end": dur - 0.5, "loud_start": loud_from,
                        "outro_start": outro if outro is not None else dur - 8.0, "body_db": body_db}}


# ── pure logic ───────────────────────────────────────────────────────────────

def test_tempo_match_decision():
    assert automix.tempo_match(120, 124, 0.08)["matched"] is True
    m = automix.tempo_match(120, 124, 0.08)
    assert m["factor"] == 1.0 and m["ratio"] == pytest.approx(120 / 124)
    half = automix.tempo_match(128, 63, 0.08)                    # half tempo counts
    assert half["matched"] and half["factor"] == 2.0
    double = automix.tempo_match(70, 142, 0.08)
    assert double["matched"] and double["factor"] == 0.5
    assert automix.tempo_match(120, 90, 0.08)["matched"] is False
    assert automix.tempo_match(120, 90, 0.08)["ratio"] == 1.0
    assert automix.tempo_match(0, 120, 0.08)["matched"] is False


def test_equal_power_gains():
    x = np.linspace(0, 1, 101)
    g_out, g_in = automix.equal_power(x)
    assert np.allclose(g_out ** 2 + g_in ** 2, 1.0)
    assert g_out[0] == pytest.approx(1) and g_in[0] == pytest.approx(0)
    assert g_out[-1] == pytest.approx(0, abs=1e-12) and g_in[-1] == pytest.approx(1)
    assert np.all(np.diff(g_out) <= 1e-12) and np.all(np.diff(g_in) >= -1e-12)


def test_level_profile_trims_silence_and_finds_outro():
    hop = 0.1
    db = np.concatenate([np.full(20, -90.0),            # 2 s silence
                         np.full(50, -32.0),            # 5 s quiet intro
                         np.full(600, -14.0),           # 60 s body
                         np.linspace(-14, -45, 100),    # 10 s fade-out
                         np.full(10, -90.0)])
    p = automix.level_profile(db, hop)
    assert p["start"] == pytest.approx(2.0)
    assert p["loud_start"] == pytest.approx(7.0, abs=1.6)
    assert 67.0 <= p["outro_start"] <= 71.0                 # where the fade gets 9 dB below the body
    assert p["end"] == pytest.approx(77.0, abs=0.2)


def test_downbeat_phase_from_accents():
    acc = [0.2, 0.2, 1.0, 0.2] * 8
    assert automix.downbeat_phase(acc) == 2
    assert automix.downbeat_phase([1.0] * 16) == 0
    assert automix.downbeat_phase([]) == 0


def test_out_point_is_a_phrase_end_before_the_outro():
    s = fake_song("a", 120, dur=120, first_beat=0.5, phase=0, outro=100.0)
    out = automix.out_point(s, earliest=10.0, overlap_src=8.0)
    phrase = 32 * 0.5                                          # 8 bars of 4 beats at 120 BPM
    assert out <= 100.05
    assert ((out - 0.5) / phrase) == pytest.approx(round((out - 0.5) / phrase), abs=1e-6)
    assert out == pytest.approx(0.5 + 6 * phrase)               # 96.5: the last phrase end before 100


def test_in_point_skips_quiet_intro_but_keeps_an_energetic_one():
    quiet = fake_song("q", 120, loud_from=20.3, phase=1)
    t = automix.in_point(quiet)
    bar = 2.0
    first_down = 0.5 + 0.5                                      # phase 1
    assert ((t - first_down) / bar) == pytest.approx(round((t - first_down) / bar), abs=1e-6)  # a downbeat
    assert 19.0 <= t <= 20.3 + 1.0
    energetic = fake_song("e", 120, loud_from=0.5, phase=0)
    assert automix.in_point(energetic) == pytest.approx(0.5)
    assert automix.in_point(energetic, first=True) == pytest.approx(0.5)     # first song: after the silence


def test_plan_mix_matched_and_unmatched_transitions():
    songs = [fake_song("a", 120), fake_song("b", 124), fake_song("c", 90)]
    plan = automix.plan_mix(songs, automix.settings({}))
    t1, t2 = plan["transitions"]
    assert t1["matched"] and t1["bars"] in (4, 8) and t1["length"] <= 12.0 + 1e-6
    assert t1["ratio"] == pytest.approx(120 / 124, rel=1e-6)
    assert not t2["matched"] and 3.0 <= t2["length"] <= 4.0
    # every song enters where the previous transition starts; boundaries inside the overlaps
    for i, t in enumerate(plan["transitions"]):
        assert plan["songs"][i + 1]["place"] == pytest.approx(t["start"])
        assert t["start"] <= t["boundary"] <= t["start"] + t["length"]
    # matched: the boundary (middle of an even number of bars) is an incoming downbeat
    assert t1["boundary"] == pytest.approx(t1["start"] + t1["length"] / 2, abs=1e-3)
    tl = automix.mix_timeline(plan, songs)
    assert tl["duration"] == pytest.approx(plan["duration"])
    assert [round(b, 3) for b in tl["boundaries"]] == [round(t["boundary"], 3) for t in plan["transitions"]]
    assert all(np.diff(tl["beats"]) > 0) and tl["beats"][-1] <= tl["duration"]
    assert set(np.round(tl["downbeats"], 3)) <= set(np.round(tl["beats"], 3))
    assert len(tl["songs"]) == 3 and all(0 <= s["energy"] <= 1 for s in tl["songs"])


def test_settings_defaults_and_overrides():
    s = automix.settings({})
    assert s["enabled"] and s["max_overlap"] == 12.0 and s["tempo_tolerance"] == 0.08 and s["target_lufs"] == -16.0
    s = automix.settings({"automix": {"max_overlap": 8, "target_lufs": -18}})
    assert s["max_overlap"] == 8.0 and s["target_lufs"] == -18.0
    assert automix.settings({"automix": False})["enabled"] is False


# ── rendering ────────────────────────────────────────────────────────────────

@requires_ffmpeg
def test_render_continuous_mix_of_synthetic_playlist(tmp_path):
    import soundfile as sf
    from social_video_editor.adapters.ffmpeg.encoder import FfmpegEncoder
    from social_video_editor.adapters.librosa.audio_analyzer import LibrosaAudioAnalyzer

    paths = [click_song(tmp_path / "s1.wav", 120), click_song(tmp_path / "s2.wav", 124, amp=0.25, pad_hz=247),
             click_song(tmp_path / "s3.wav", 90, quiet_intro=6.0, pad_hz=196)]
    enc, an = FfmpegEncoder(), LibrosaAudioAnalyzer()
    cfg = automix.settings({})
    songs = []
    for p in paths:
        am = an.music(p)
        songs.append(automix.song(p.name, am, enc.music_features(p, am, tmp_path / "cache"), cfg))
    plan = automix.plan_mix(songs, cfg)
    assert plan["transitions"][0]["matched"] and not plan["transitions"][1]["matched"]
    assert plan["songs"][2]["in"] >= 1.5 + 5.0                  # the quiet intro of song 3 is skipped
    wav = enc.render_automix(paths, plan, tmp_path / "cache")
    assert enc.render_automix(paths, plan, tmp_path / "cache") == wav       # cached
    x, sr = sf.read(wav)
    assert sr == 48000 and x.shape[1] == 2
    assert len(x) / sr == pytest.approx(plan["duration"], abs=0.01)         # expected length
    # continuous: no silence anywhere but in the final fade
    body = x[: int((plan["duration"] - plan["songs"][-1]["fade_out"]) * sr)].mean(axis=1)
    assert silences(body, sr, -50.0, 0.3) == []
    assert np.abs(x).max() <= 10 ** (-1.4 / 20)                              # no clipping (ceiling −1.5 dBFS)

    def level(a, b):
        seg = x[int(a * sr):int(b * sr)]
        return 10 * np.log10(np.mean(seg ** 2) + 1e-12)

    for t in plan["transitions"]:                                            # no dip > 2 dB at the splices
        a, b = t["start"], t["start"] + t["length"]
        around = min(level(a - 4, a - 1), level(b + 1, b + 4))
        inside = min(level(u, u + 1) for u in np.arange(a, b - 1 + 1e-9, 0.5))
        assert around - inside < 2.0
    tl = automix.mix_timeline(plan, songs)
    assert tl["duration"] == pytest.approx(len(x) / sr, abs=0.01)


# ── sve check helpers for splices ────────────────────────────────────────────

def test_splice_check_helpers():
    from social_video_editor.application.check import _limit, _splice_dip, _true_peak_db

    t = np.arange(0, 40, 0.1)
    steady = np.full(len(t), -20.0)
    assert _splice_dip(t, steady, 15, 25) == pytest.approx(0.0, abs=1e-6)
    dipped = steady.copy()
    dipped[(t >= 19) & (t < 21)] = -26.0                      # a 2 s hole of 6 dB at the splice
    assert _splice_dip(t, dipped, 15, 25) == pytest.approx(6.0, abs=0.01)
    x = np.zeros((4800, 2))
    x[2400] = 0.5
    assert _true_peak_db(x) == pytest.approx(20 * np.log10(0.5), abs=0.5)
    assert _limit(None) is None and _limit(0) is None and _limit("60") == 60.0


def test_short_crossfade_make_up_gain_fills_the_dip():
    from social_video_editor.adapters.ffmpeg.automix_render import level_db, soft_limit, splice_makeup

    sr, n = 48000, int(3.5 * 48000)
    rng = np.random.default_rng(7)
    a = (0.1 * rng.standard_normal((n, 2))).astype(np.float32)          # outgoing song
    b = (0.06 * rng.standard_normal((n, 2))).astype(np.float32)         # incoming: a quieter intro
    pos = (np.arange(n) + 0.5) / n
    mixed = a * ((1 - pos) ** 2)[:, None] + b * (pos ** 2)[:, None]      # fades too steep: a deep dip
    ref_out, ref_in = level_db(a), level_db(b)

    def worst_dip(x):
        return min(ref_out, ref_in) - min(level_db(x[i:i + sr]) for i in range(0, len(x) - sr + 1, sr // 2))

    assert worst_dip(mixed) > 2.0                                        # the problem: > 2 dB below
    g = splice_makeup(mixed, ref_out, ref_in, sr)
    fixed = soft_limit(mixed * g[:, None])
    assert worst_dip(fixed) < 1.5
    assert g.max() <= 10 ** (6.0 / 20) + 1e-6 and g.min() >= 1.0
    assert g[0] == pytest.approx(1.0) and g[-1] == pytest.approx(1.0)  # seamless joints
    assert np.abs(np.diff(20 * np.log10(g))).max() < 0.01                # slow, click-free gain
    assert np.abs(fixed).max() <= 10 ** (-1.5 / 20) + 1e-6               # peaks still under the ceiling
    flat = (0.1 * rng.standard_normal((n, 2))).astype(np.float32)        # no dip → no gain
    assert np.all(splice_makeup(flat, level_db(flat), level_db(flat), sr) == 1.0)
