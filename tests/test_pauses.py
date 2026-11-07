"""Talk recipe: keep all the speech, shorten only the long pauses (domain/pauses.py + talk plan)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import requires_ffmpeg
from social_video_editor.adapters.files.legacy_es import CONFIG, translate
from social_video_editor.adapters.system.tools import FFMPEG, FFPROBE
from social_video_editor.domain.pauses import settings, shorten_plan_pauses, speech_spans


def test_short_pauses_are_left_alone():
    assert speech_spans([(2.0, 2.6), (5.0, 5.8)], 10.0) == [(0.0, 10.0)]


def test_long_pause_shortened_to_keep_pause():
    spans = speech_spans([(4.0, 7.0)], 10.0, min_pause=0.9, keep_pause=0.4)
    assert spans == [(0.0, 4.2), (6.8, 10.0)]
    kept = (spans[0][1] - 4.0) + (7.0 - spans[1][0])
    assert kept == pytest.approx(0.4)


def test_overlap_is_added_so_the_crossfade_keeps_the_pause():
    (a0, b0), (a1, b1) = speech_spans([(4.0, 7.0)], 10.0, keep_pause=0.4, overlap=0.1)
    assert (b0 - 4.0) + (7.0 - a1) - 0.1 == pytest.approx(0.4)


def test_leading_and_trailing_silence_trimmed():
    spans = speech_spans([(0.0, 2.0), (8.0, 10.0)], 10.0, keep_pause=0.4)
    assert spans == [(1.8, 8.2)]


def test_tiny_span_keeps_the_pause_before_it():
    # 0.05 s of sound between two long pauses: no tiny shot, the first pause stays instead
    spans = speech_spans([(2.0, 4.0), (4.05, 6.0)], 8.0, keep_pause=0.4, min_span=0.5)
    assert len(spans) == 2
    assert spans[0][0] == 0.0 and spans[0][1] > 4.05


def test_settings_defaults_and_overrides():
    s = settings({"pauses": {"min_pause": 0.6, "keep_pause": None}})
    assert s["min_pause"] == 0.6 and s["keep_pause"] == 0.45


def test_spanish_keys():
    out = translate({"charla": True, "pausas": {"pausa_minima": 0.6, "pausa_dejada": 0.4}}, CONFIG)
    assert out == {"talk": True, "pauses": {"min_pause": 0.6, "keep_pause": 0.4}}


def _analysis(duration=10.0, quiet=((4.0, 6.0),), words=True):
    """Fake analysis: speech at -20 dB except the `quiet` stretches (-60 dB), levels every 0.1 s."""
    n = int(duration * 10)
    db = [-60.0 if any(a <= i / 10 < b for a, b in quiet) else -20.0 for i in range(n)]
    ws = []
    if words:
        t = 0.0
        while t < duration - 0.4:
            if not any(a - 0.4 < t < b for a, b in quiet):
                ws.append({"word": "x", "start": round(t, 2), "end": round(t + 0.3, 2), "prob": 1})
            t += 0.4
    segs = [[0.0, quiet[0][0]], [quiet[-1][1], duration]] if quiet else [[0.0, duration]]
    return {"duration": duration, "audio": {"step": 0.1, "db": db, "median_db": -20.0},
            "words": ws, "voice": {"segments": segs, "ratio": 0.8}}


CFG = {"transitions": {}, "pauses": {}}


def test_plan_shot_with_voice_is_split_and_indices_renumbered():
    plan = {"shots": [{"source": 0, "start": 1.0, "end": 9.0, "speed": 1, "transition": "cut",
                       "effects": [{"type": "zoom_punch", "t": 5.0}], "role": "story"},
                      {"type": "polaroid", "from_shot": 0, "transition": "cut"}],
            "texts": [{"text": "a", "shot": 0, "until_shot": 1}, {"text": "b", "shot": 1}]}
    assert shorten_plan_pauses(plan, [_analysis()], CFG) == 1
    a, b, pol = plan["shots"]
    assert a["end"] < 4.5 and b["start"] > 5.5
    assert b["transition"] == "soft_cut" and b["pause_cut"] is True
    assert b["effects"] and not a["effects"]               # the zoom at 5 s now happens in the 2nd piece
    assert pol["from_shot"] == 1                           # last frame of the old shot 0 = 2nd piece
    assert plan["texts"][0] == {"text": "a", "shot": 0, "until_shot": 2}
    assert plan["texts"][1]["shot"] == 2


def test_plan_shots_left_alone():
    an = _analysis()
    shots = [{"source": 0, "start": 1.0, "end": 9.0, "role": "hook"},
             {"source": 0, "start": 1.0, "end": 9.0, "beats": 8},
             {"source": 0, "start": 1.0, "end": 9.0, "speed": 1.3}]
    plan = {"shots": [dict(s) for s in shots], "texts": []}
    assert shorten_plan_pauses(plan, [an], CFG) == 0
    assert plan["shots"] == shots
    off = {"shots": [{"source": 0, "start": 1.0, "end": 9.0}], "texts": []}
    assert shorten_plan_pauses(off, [an], {"transitions": {}, "pauses": {"enabled": False}}) == 0


def test_quiet_stretch_with_a_word_inside_is_not_a_pause():
    an = _analysis()
    an["words"].append({"word": "shh", "start": 4.8, "end": 5.2, "prob": 1})   # a whispered word
    plan = {"shots": [{"source": 0, "start": 1.0, "end": 9.0}], "texts": []}
    assert shorten_plan_pauses(plan, [an], CFG) == 0


def _duration(path: Path, stream: str) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", stream, "-show_entries",
                          "stream=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


@pytest.mark.integration
@requires_ffmpeg
def test_talk_plan_and_render(workspace, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")             # Whisper never downloads models in tests
    monkeypatch.chdir(workspace.parent)
    clip = workspace / "talk.mp4"
    # tone with a 2 s pause (3–5 s, shortened) and a 0.5 s pause (8–8.5 s, kept)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=10:r=30",
                    "-f", "lavfi", "-i",
                    "aevalsrc='0.5*sin(2*PI*440*t)*(lt(t,3)+between(t,5,8)+gt(t,8.5))':d=10:s=48000",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(clip)], check=True)
    d = workspace / "projects" / "talk"
    d.mkdir(parents=True)
    (d / "project.yaml").write_text(yaml.safe_dump(
        {"name": "talk", "recipe": "talk", "sources": [{"path": str(clip)}],
         "settings": {"transcription": {"model": "tiny"}}}), encoding="utf-8")
    from social_video_editor.cli import main

    main(["plan", "talk"])
    shots = json.loads((d / "plan.json").read_text())["shots"]
    assert [s["transition"] for s in shots] == ["cut", "soft_cut"]
    assert shots[0]["end"] == pytest.approx(3.285, abs=0.11)       # 3 s + 0.45 / 2 + 0.12 / 2
    assert shots[1]["start"] == pytest.approx(4.715, abs=0.11)

    main(["render", "talk", "--draft", "--no-music"])
    final = next((d).glob("v1*/*.mp4"))
    expected = 10.0 - 2.0 + 0.45                                   # one pause shortened to 0.45 s
    assert _duration(final, "v:0") == pytest.approx(expected, abs=0.15)
    assert abs(_duration(final, "v:0") - _duration(final, "a:0")) < 0.1
