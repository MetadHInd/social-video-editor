"""Breaths lowered, the optional leveler and the music measured against the voice
(domain/voice_levels.py, adapters/ffmpeg/voice.py, adapters/ffmpeg/voice_mix.py) — synthetic audio only."""
from __future__ import annotations

import subprocess

import numpy as np
import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg import filters, voice, voice_mix
from social_video_editor.adapters.ffmpeg.loudness import ebur128
from social_video_editor.adapters.files import legacy_es
from social_video_editor.adapters.system.tools import FFMPEG
from social_video_editor.domain import check as ck
from social_video_editor.domain import voice_levels as vl

F = vl.FRAME


def _track(parts: list[tuple[float, float]]) -> np.ndarray:
    """A level track (dB per frame) from (seconds, level) pieces; speech pieces flicker like syllables."""
    out = []
    for sec, level in parts:
        n = int(round(sec / F))
        x = np.full(n, float(level))
        if level > -30:                                   # "speech": 3 dB syllable ripple
            x += 3 * np.sin(np.arange(n) * 2 * np.pi * F * 4)
        out.append(x)
    return np.concatenate(out)


# ── pure ─────────────────────────────────────────────────────────────────────

def test_breaths_between_sentences_are_found_and_words_are_kept():
    # 1 s speech, 0.15 s silence, 0.4 s breath (−42), 0.15 s silence, 1 s speech, 0.06 s gap (a closure), 0.5 s speech
    db = _track([(1.0, -20), (0.15, -65), (0.4, -42), (0.15, -65), (1.0, -20), (0.06, -60), (0.5, -20)])
    regions = vl.breath_regions(db, vl.BREATHS)
    assert len(regions) == 1                              # the closure inside the speech is not a pause
    a, b = regions[0]
    assert a == pytest.approx(1.0 + 0.10, abs=0.02) and b == pytest.approx(1.7 - 0.06, abs=0.02)
    assert vl.breath_stats(db, regions) == {"pauses": 1, "with_sound": 1, "seconds": round(b - a, 2)}
    # a word the transcription heard in the pause is never touched
    word = [{"word": "eh", "start": 1.30, "end": 1.45}]
    for a2, b2 in vl.breath_regions(db, vl.BREATHS, word):
        assert b2 <= 1.30 - 0.06 or a2 >= 1.45 + 0.10 - 1e-9
    # gain: 0 in the speech, −depth in the middle of the pause, fades inside it
    g = vl.gain_curve(regions, len(db), 12.0, 0.04)
    assert g[:int(a / F)].max() == 0 and g[int(b / F) + 1:].max() == 0
    assert g[int((a + b) / 2 / F)] == pytest.approx(-12)
    assert -12 < g[int(a / F)] < 0                         # the fade starts inside the pause


def test_a_breath_hovering_over_the_threshold_is_not_speech():
    db = _track([(1.0, -15), (0.5, -36), (1.0, -15)])     # breath 21 dB under: never reaches a syllable's level
    sp = vl.speech_mask(db)
    assert not sp[int(1.2 / F):int(1.3 / F)].any() and sp[int(0.5 / F)] and sp[int(2.0 / F)]


def test_polish_settings():
    assert vl.polish_settings({}) is None and vl.polish_settings({"breaths": False}) is None
    p = vl.polish_settings({"breaths": True}, [{"word": "a", "start": 1, "end": 1.5}, {"word": "b"}])
    assert p == {"breaths": vl.BREATHS, "leveler": False, "words": [[1.0, 1.5]]}
    assert vl.polish_settings({"breaths": {"depth_db": 8}})["breaths"]["depth_db"] == 8
    assert vl.polish_settings({"voice_leveler": True}) == {"breaths": None, "leveler": True, "words": []}
    assert voice.clean_chain(None, highpass=0, leveler=True).endswith(voice.LEVELER)
    assert voice.LEVELER not in voice.clean_chain(None)
    assert legacy_es.config({"audio": {"respiraciones": True, "nivelador_voz": False},
                             "music": {"relativa_a_voz": True, "bajo_voz_db": 9, "bajo_pausa_db": 3,
                                       "entrada_mas_db": 2},
                             "check": {"musica_min_bajo_voz_lu": 5}}) == {
        "audio": {"breaths": True, "voice_leveler": False},
        "music": {"relative_to_voice": True, "under_voice_db": 9, "under_pause_db": 3, "intro_db": 2},
        "check": {"music_min_under_voice_lu": 5}}


def test_music_curve_follows_the_voice():
    s = vl.music_settings({})
    speech = [(2.0, 4.0), (4.3, 6.0), (8.0, 10.0)]        # 0.3 s gap (stays down), 2 s pause (comes up)
    c = vl.music_curve(speech, 12.0, s)
    at = lambda t: c[int(t / F)]                          # noqa: E731
    assert at(0.5) == pytest.approx(4)                    # intro: 4 dB over the pause level
    assert at(2.0) == pytest.approx(-6) and at(1.9) < 4   # already down at the first word (it started earlier)
    assert at(4.15) == pytest.approx(-6)                  # short gap: no pumping
    assert at(7.0) == pytest.approx(0)                    # a real pause: under_pause level
    assert at(8.0) == pytest.approx(-6) and at(11.5) == pytest.approx(0)
    assert np.abs(np.diff(c)).max() <= 10 / (s["attack"] / F) + 1e-9   # slew-limited: no steps
    assert vl.music_base_gain(-20, -12, s) == pytest.approx(-12)       # pauses at −24 LUFS = voice − 4
    no_intro = vl.music_curve(speech, 12.0, vl.music_settings({"intro_db": 0}))
    assert no_intro[int(0.5 / F)] == pytest.approx(0)


def test_music_balance_and_speech_moments():
    v = [-20.0] * 10 + [-45.0] * 5 + [-20.0] * 10
    m = [-30.0] * 10 + [-24.0] * 5 + [-30.0] * 10
    sp = ck.speech_moments(v, -20.0)
    assert sp == [True] * 10 + [False] * 5 + [True] * 10
    b = vl.music_balance(-20.0, v, m, sp)
    assert b["speech_lu"] == 10 and b["pause_lu"] == 4 and b["worst_lu"] == 10
    lines = ck.report_lines({"version": "v1", "ok": True, "errors": [], "warnings": [], "listen": [],
                             "metrics": {"music_vs_voice": {"speech_lu": 10, "pause_lu": 4, "worst_lu": 9,
                                                            "worst_t": 1.0}}}, [])
    assert any("music under the voice: 10 LU while it speaks, 4 LU in pauses" in x for x in lines)


def test_the_fixed_music_chain_is_unchanged_without_the_bed(cfg):
    cfg = {**cfg, "music": {**cfg["music"], "_start": 1.5}}
    graph, out = filters.final_mix("[a]", 10.0, cfg, 3, None, True)
    assert graph == (
        "[a]asplit=2[vz][sc];[3:a]aresample=48000,aformat=channel_layouts=stereo,atrim=start=1.500,"
        "asetpts=PTS-STARTPTS,aloop=loop=-1:size=2147483647,atrim=0:10.0000,volume=-11.0dB,afade=t=in:d=0.25,"
        "afade=t=out:st=9.000:d=1.000[mus0];[mus0][sc]sidechaincompress=threshold=0.04:ratio=8:attack=20:"
        "release=300[mus];[vz][mus]amix=inputs=2:normalize=0:duration=first[amix]")
    bed, _ = filters.final_mix("[a]", 10.0, {**cfg, "music": {**cfg["music"], "_bed": True}}, 3, None, True)
    assert bed == ("[3:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:10.0000[mus];"
                   "[a][mus]amix=inputs=2:normalize=0:duration=first[amix]")
    # the bed's raw chain is the fixed chain without its volume
    fixed = graph.split(";")[1].removesuffix("[mus0]")
    assert voice_mix.raw_chain(cfg["music"], 10.0, 3) == fixed.replace(",volume=-11.0dB", "")
    assert voice_mix.energy_mean([(-20, 1), (-20, 3)]) == pytest.approx(-20)
    assert voice_mix.energy_mean([]) == float("-inf")


# ── with FFmpeg ──────────────────────────────────────────────────────────────

def _speech_with_breath(path, sr=48000):
    """1.5 s of "speech" (a tone with a 4 Hz syllable envelope), a 0.4 s breath (soft high-passed noise)
    inside a 0.8 s pause, then 1.5 s more speech."""
    subprocess.run([FFMPEG, "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency=220:duration=1.5:sample_rate={sr}",
                    "-f", "lavfi", "-i", f"anoisesrc=d=0.4:c=pink:r={sr}:a=0.02",
                    "-f", "lavfi", "-i", f"sine=frequency=180:duration=1.5:sample_rate={sr}",
                    "-filter_complex",
                    "[0]volume='0.35*(0.55+0.45*sin(2*PI*4*t))':eval=frame[s1];"
                    "[1]highpass=f=1500,adelay=200,apad=whole_dur=0.8[br];"
                    "[2]volume='0.35*(0.55+0.45*sin(2*PI*4*t))':eval=frame[s2];"
                    "[s1][br][s2]concat=n=3:v=0:a=1,aformat=channel_layouts=stereo",
                    "-c:a", "pcm_s16le", str(path)], check=True)


def _rms(path, a, b):
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", str(a), "-t", str(b - a), "-i", str(path), "-ac", "1",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9))


@requires_ffmpeg
def test_the_breath_is_lowered_and_the_speech_is_not(tmp_path):
    src = tmp_path / "rec.wav"
    _speech_with_breath(src)
    info = {"path": str(src), "audio_index": 0}
    plain = voice.clean_voice(info, tmp_path / "plain.wav", None, highpass=0)
    polished = voice.clean_voice(info, tmp_path / "polished.wav", None, highpass=0,
                                 polish=vl.polish_settings({"breaths": True}))
    assert not list(tmp_path.glob("*.part*")) and not list(tmp_path.glob("*.env*"))
    assert (tmp_path / "polished.json").is_file()
    breath = (1.5 + 0.30, 1.5 + 0.55)
    assert _rms(polished, *breath) < _rms(plain, *breath) - 9
    for a, b in ((0.2, 1.4), (2.35, 3.2)):                # the speech, including the word start at 2.3 s
        assert _rms(polished, a, b) == pytest.approx(_rms(plain, a, b), abs=0.2)
    assert _rms(polished, 2.30, 2.40) == pytest.approx(_rms(plain, 2.30, 2.40), abs=0.3)   # onset intact
    probe = subprocess.run([FFMPEG, "-i", str(polished)], capture_output=True, text=True).stderr
    assert "48000 Hz, stereo" in probe and "00:00:03.80" in probe


@requires_ffmpeg
def test_the_music_bed_sits_under_the_voice(tmp_path):
    # two segments: speech 1.5–4.0 s, a 2 s pause, speech 6.0–8.5 s (a cut in the pause, no overlap)
    one, two = tmp_path / "s1.wav", tmp_path / "s2.wav"
    for f, lead in ((one, 1.5), (two, 0.0)):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=2.5:sample_rate=48000",
                        "-af", f"volume='0.3*(0.6+0.4*sin(2*PI*4*t))':eval=frame,adelay={int(lead * 1000)}:all=1,"
                        f"apad=whole_dur={6.0 if f == one else 3.0},aformat=channel_layouts=stereo",
                        "-c:a", "pcm_s16le", str(f)], check=True)
    music = tmp_path / "bed_src.wav"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=12:c=pink:r=48000:a=0.3",
                    "-ac", "2", "-c:a", "pcm_s16le", str(music)], check=True)
    cfg = {"music": {"_start": 0.0, "fade_in": 0.25, "fade_out": 0.01, "relative_to_voice": True}}
    segs = [{"dur": 6.0, "overlap": 0, "transition": "cut"}, {"dur": 3.0, "overlap": 0, "transition": "cut"}]
    bed, new_cfg = voice_mix.music_bed(segs, [one, two], music, cfg, 9.0, tmp_path / "work")
    try:
        lv = new_cfg["music"]["_levels"]
        assert new_cfg["music"]["_bed"] and lv["speech_spans"] == 2
        V = lv["voice_lufs"]
        L = ebur128(bed, series=True)
        mom = dict(zip((round(t, 1) for t in L["t"]), L["momentary"]))
        assert mom[3.0] == pytest.approx(V - 10, abs=1.5)        # while it speaks
        assert mom[5.3] == pytest.approx(V - 4, abs=1.5)         # in the long pause
        assert mom[1.0] == pytest.approx(V, abs=1.5)             # intro (+4 over the pause level)
        assert not list((tmp_path / "work").glob("_music_raw*")) and not list((tmp_path / "work").glob("*.f32"))
    finally:
        bed.unlink(missing_ok=True)
    # no voice at all → the fixed mix
    silent = tmp_path / "silent.wav"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3",
                    "-c:a", "pcm_s16le", str(silent)], check=True)
    assert voice_mix.music_bed([{"dur": 3.0, "overlap": 0, "transition": "cut"}], [silent], music, cfg, 3.0,
                               tmp_path / "work") is None
