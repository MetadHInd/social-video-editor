"""Cutaways rendered for real: clips laid over the joined picture (the sound untouched), transparent
overlays, and an explainer project from script.md to the video. Synthetic media in the temporary
workspace; the transcription is written to the cache (no speech model is run)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg import filters
from social_video_editor.adapters.ffmpeg.encoder import FfmpegEncoder
from social_video_editor.adapters.system.tools import FFMPEG, FFPROBE
from social_video_editor.application.analyze import prepare_source
from social_video_editor.container import build_services
from social_video_editor.domain import pip
from social_video_editor.domain.cutaways import soften

W, H, FPS = 180, 320, 30
RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 200, 30)


def solid(color, n: int, alpha: int | None = None):
    pixel = bytes(color) if alpha is None else bytes((*color, alpha))
    for _ in range(n):
        yield pixel * (W * H)


def frame(video: Path, t: float) -> np.ndarray:
    """The frame at second `t` as an H×W×3 array."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    n = len(raw) // 3
    h = int(round((n * H / W) ** 0.5))
    return np.frombuffer(raw, np.uint8).reshape(h, n // h, 3)


def tint(video: Path, t: float, box=(0.1, 0.9, 0.1, 0.5)) -> str:
    """Dominant channel ("red" / "green" / "blue") of a part of the frame (x0, x1, y0, y1 as shares)."""
    f = frame(video, t)
    h, w = f.shape[:2]
    part = f[int(box[2] * h):int(box[3] * h), int(box[0] * w):int(box[1] * w)].reshape(-1, 3).mean(axis=0)
    return ("red", "green", "blue")[int(part.argmax())]


def duration(path: Path, stream: str) -> float:
    """Seconds of a stream, counted from what it decodes to (works for any container)."""
    if stream.startswith("a"):
        raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                             capture_output=True, check=True).stdout
        return len(raw) / 2 / 48000
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", stream, "-count_frames", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return int(out.strip().split("\n")[0]) / FPS


def loudness(video: Path, start: float, end: float) -> float:
    """RMS (0–1) of the audio between two seconds."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(video),
                          "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float64) / 32768
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


# ── the filter and the composition ───────────────────────────────────────────
def test_cutaway_filter_shifts_each_clip_to_its_start():
    text, label = filters.cutaways("[v1]", [{"start": 1.0, "end": 2.0}, {"start": 3.0, "end": 4.5, "alpha": True}], 5, 30)
    assert "[5:v]fps=30,setpts=PTS-STARTPTS+1.000000/TB,tpad=stop_mode=clone:stop_duration=0.0667[cut0]" in text
    assert "[v1][cut0]overlay=0:0:eof_action=pass:enable='between(t,0.983333,1.983333)'[vcut0]" in text
    assert "[6:v]fps=30,format=rgba,setpts=PTS-STARTPTS+3.000000/TB,tpad=" in text
    assert "[vcut0][cut1]overlay=0:0:format=auto:eof_action=pass:enable='between(t,2.983333,4.483333)'[vcut1]" in text
    assert label == "[vcut1]"


def test_cutaway_filter_puts_clips_on_the_frame_grid_and_fades_them():
    items = [{"start": 1.59, "end": 3.053, "fade_in": 0.2, "hold": 0.2333}, {"start": 3.053, "end": 4.0, "fade_in": 0.2,
                                                                            "fade_out": 0.2}]
    text, _ = filters.cutaways("[v1]", items, 1, 30)
    assert "setpts=PTS-STARTPTS+1.600000/TB" in text                 # 1.59 s is not a frame: 1.6 s is
    assert "tpad=stop_mode=clone:stop_duration=0.3000" in text       # held under the next one, + 2 frames
    assert "between(t,1.583333,3.283300)" in text                    # its window reaches into the next one
    assert "format=yuva420p,fade=t=in:st=1.600000:d=0.2000:alpha=1" in text
    assert "fade=t=out:st=3.800000:d=0.2000:alpha=1" in text


def solid_colors(items: list[dict], fps: int = 30) -> str:
    """The composed picture, frame by frame: R = the video under the clips, B / G = the clips, ? = a mix."""
    cmd = [FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=red:s=32x32:r={fps}:d=4"]
    for c in items:
        n = int(round((c["end"] - c["start"]) * fps))
        cmd += ["-f", "lavfi", "-i", f"color={c['color']}:s=32x32:r={fps},trim=end_frame={n}"]
    text, label = filters.cutaways("[0:v]", items, 1, fps)
    raw = subprocess.run(cmd + ["-filter_complex", text, "-map", label, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    px = np.frombuffer(raw, np.uint8).reshape(-1, 32, 32, 3)[:, 16, 16]
    return "".join("R" if p[0] > 230 and p[2] < 25 else "B" if p[2] > 230 and p[0] < 25
                   else "G" if p[1] > 100 and p[0] < 25 and p[2] < 25 else "?" for p in px)


@requires_ffmpeg
def test_two_clips_in_a_row_never_show_the_picture_under_them():
    # the first starts between two frames: before, one frame of the presenter showed where they meet
    frames = solid_colors([{"start": 1.59, "end": 3.053, "color": "blue"}, {"start": 3.053, "end": 3.5, "color": "green"}])
    inside = frames[48:105]
    assert "R" not in inside and "B" in inside and "G" in inside


@requires_ffmpeg
def test_faded_clips_dissolve_over_each_other_not_over_the_presenter():
    items = soften([{"start": 0.59, "end": 1.59, "color": "blue"}, {"start": 1.59, "end": 2.6, "color": "green"}],
                   0.2, 30, 4.0)
    frames = solid_colors(items)
    assert "?" in frames[17:24]                       # red → blue: a dissolve, not a cut
    between = frames[24:78]
    assert "R" not in between and "?" in between      # blue → green: one over the other
    assert "?" in frames[72:80] and frames[-1] == "R"  # green → back to the presenter, softly


def test_presenter_window_reads_the_joined_picture_and_uses_no_input():
    items = [{"file": "a.mkv", "start": 1.0, "end": 3.0},
             {"pip": True, "start": 1.0, "end": 3.0, "crop": (10, 20, 90, 110), "rect": (80, 150, 60, 74)},
             {"file": "frame.mov", "start": 1.0, "end": 3.0, "alpha": True}]
    text, label = filters.cutaways("[v1]", items, 4, 30)
    assert text.startswith("[v1]split=2[vjoined][pipsrc1];[4:v]")           # the picture before any cutaway, kept aside
    assert "[vjoined][cut0]overlay=0:0:eof_action=pass" in text
    assert ("[pipsrc1]crop=90:110:10:20,scale=60:74:flags=bicubic[pip1];"
            "[vcut0][pip1]overlay=80:150:eof_action=pass:enable='between(t,0.983333,2.983333)'[vcut1]") in text
    assert "[5:v]fps=30,format=rgba," in text and label == "[vcut2]"          # the frame is the next input, not the 6th


@pytest.fixture
def base(tmp_path, cfg):
    """Two red 1.5 s segments joined with a cut (3 s), and what compose needs."""
    enc = FfmpegEncoder()
    files = [enc.encode_frames(solid(RED, 45), 45, W, H, FPS, tmp_path / f"seg{k}.mkv", draft=True) for k in range(2)]
    cfg["music"]["file"] = "none"
    segments = [{"dur": 1.5, "overlap": 0.0, "transition": "cut"}] * 2

    def compose(name: str, cutaways=None) -> Path:
        work = tmp_path / name
        work.mkdir()
        return enc.compose(segments, files, None, None, None, cfg, W, H, 3.0, False, True, work, work / "out.mkv",
                           cutaways=cutaways)
    return enc, compose, tmp_path


@pytest.mark.integration
@requires_ffmpeg
def test_cutaway_shows_between_its_times_and_the_audio_keeps_its_length(base):
    enc, compose, tmp = base
    blue = enc.encode_frames(solid(BLUE, 30), 30, W, H, FPS, tmp / "blue.mkv", draft=True)
    plain = compose("plain")
    out = compose("cut", [{"file": blue, "start": 1.0, "end": 2.0, "alpha": False}])
    assert [tint(out, t) for t in (0.5, 0.95, 1.02, 1.5, 1.95, 2.02, 2.5)] == [
        "red", "red", "blue", "blue", "blue", "red", "red"]
    assert tint(plain, 1.5) == "red"
    assert duration(out, "a:0") == pytest.approx(duration(plain, "a:0"), abs=0.001)
    assert duration(out, "v:0") == pytest.approx(3.0, abs=0.05)


@pytest.mark.integration
@requires_ffmpeg
def test_two_cutaways_and_one_that_crosses_the_cut(base):
    enc, compose, tmp = base
    blue = enc.encode_frames(solid(BLUE, 24), 24, W, H, FPS, tmp / "blue.mkv", draft=True)
    green = enc.encode_frames(solid(GREEN, 30), 30, W, H, FPS, tmp / "green.mkv", draft=True)
    out = compose("two", [{"file": blue, "start": 0.2, "end": 1.0}, {"file": green, "start": 1.2, "end": 2.2}])
    assert [tint(out, t) for t in (0.1, 0.6, 1.1, 1.5, 2.0, 2.6)] == ["red", "blue", "red", "green", "green", "red"]


@pytest.mark.integration
@requires_ffmpeg
def test_transparent_overlay_only_covers_what_it_draws(base):
    enc, compose, tmp = base

    def half_green(n):
        row = bytes((*GREEN, 255)) * (W // 2) + bytes((0, 0, 0, 0)) * (W - W // 2)
        for _ in range(n):
            yield row * H
    clip = enc.encode_overlay(half_green(30), 30, W, H, FPS, tmp / "overlay.mov")
    out = compose("alpha", [{"file": clip, "start": 1.0, "end": 2.0, "alpha": True}])
    left, right = (0.05, 0.4, 0.1, 0.9), (0.6, 0.95, 0.1, 0.9)
    assert (tint(out, 1.5, left), tint(out, 1.5, right)) == ("green", "red")
    assert (tint(out, 0.5, left), tint(out, 2.5, left)) == ("red", "red")


@pytest.mark.integration
@requires_ffmpeg
def test_presenter_window_shows_the_main_picture_over_the_cutaway(base):
    enc, compose, tmp = base
    blue = enc.encode_frames(solid(BLUE, 30), 30, W, H, FPS, tmp / "blue.mkv", draft=True)
    plain = compose("plain")
    out = compose("pip", [{"file": blue, "start": 1.0, "end": 2.0, "alpha": False},
                          {"pip": True, "start": 1.0, "end": 2.0, "crop": (20, 40, 90, 110), "rect": (100, 190, 60, 74)}])
    window, elsewhere = (0.60, 0.84, 0.63, 0.80), (0.05, 0.45, 0.05, 0.5)
    assert (tint(out, 1.5, window), tint(out, 1.5, elsewhere)) == ("red", "blue")     # the presenter over the cutaway
    assert (tint(out, 0.5, window), tint(out, 2.5, elsewhere)) == ("red", "red")
    assert duration(out, "a:0") == pytest.approx(duration(plain, "a:0"), abs=0.001)


# ── an explainer project, from the script to the video ───────────────────────
WORDS = [("How", 1.0), ("does", 1.3), ("a", 1.6), ("message", 1.9), ("travel?", 2.2),
         ("And", 4.0), ("they", 4.3), ("travel", 4.6), ("apart.", 4.9),                       # a first try
         ("Every", 6.0), ("time", 6.3), ("you", 6.6), ("send", 6.9), ("one,", 7.2), ("it", 7.5), ("splits", 7.8),
         ("again.", 8.1),
         ("And", 10.0), ("they", 10.3), ("travel", 10.6), ("apart.", 10.9)]                   # said again: this one counts


def explainer_project(workspace, script: str, settings: dict | None = None) -> Path:
    """A project with the explainer recipe: a red 13 s clip with a tone, its transcription written to
    the cache as if WORDS had been said."""
    clip = workspace / "presenter.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0xC81E1E:s=360x640:d=13:r={FPS}",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=13:sample_rate=48000",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(clip)], check=True)
    d = workspace / "projects" / "demo"
    d.mkdir(parents=True)
    (d / "project.yaml").write_text(yaml.safe_dump(
        {"name": "demo", "recipe": "explainer", "sources": [{"path": str(clip)}],
         "settings": {"framing": {"face": False}, "color": {"auto": False}, "pip": {"enabled": False},
                      "script": {"support": False, "auto_broll": False}, **(settings or {})}}), encoding="utf-8")
    (d / "script.md").write_text(script, encoding="utf-8")
    Image.new("RGB", (360, 640), BLUE).save(d / "board.png")
    svc = build_services()
    project = svc.projects.load(d)
    info = prepare_source(svc, project.sources[0], project.cache, project.cfg)
    words = [{"word": w, "start": t, "end": t + 0.25, "prob": 1.0} for w, t in WORDS]
    svc.cache.save(project.cache, "analysis", info["id"], {
        "id": info["id"], "duration": 13.0, "samples": [], "scenes": [], "words": words,
        "audio": {"step": 0.1, "db": [-20.0] * 130, "peaks": [], "median_db": -20.0},
        "voice": {"segments": [[1.0, 2.5], [4.0, 5.2], [6.0, 8.4], [10.0, 11.2]], "ratio": 0.5},
        "face": {"x": 0.5, "y": 0.35, "size": 0.25, "eyes": 0.32, "ratio": 0.95, "frames": 13, "detector": "test"}})
    return d


SCRIPT = """# what is said, and what is on screen
How does a message travel? And they travel apart.
[photo file="board.png"]
Every time you send one, it splits again.
[camera]
This one was never recorded.
"""


@pytest.mark.integration
@requires_ffmpeg
def test_explainer_from_script_to_video(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, SCRIPT)
    from social_video_editor.cli import main

    main(["plan", "demo"])
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    shots = plan["shots"]
    assert [s["sentences"] for s in shots] == [[0], [1], [2]]
    assert [round(s["start"], 2) for s in shots] == [0.92, 9.82, 5.82]          # script order; the second "And that…"
    assert [s["transition"] for s in shots] == ["cut", "dissolve", "dissolve"]  # 0.1 s more on each side of a dissolve
    assert [s.get("zoom") for s in shots] == [None, 1.08, 1.08]                 # a punch-in at the visible jump cut only
    assert all((s["focus_x"], s["focus_y"]) == (0.5, 0.35) for s in shots)
    assert plan["sentences"][1]["takes"] == 2 and plan["sentences"][3] == {
        "n": 3, "text": "This one was never recorded.", "missing": True}
    assert plan["cutaways"] == [{"type": "photo", "file": "board.png", "at_sentence": 2, "until_sentence": 2}]

    capsys.readouterr()
    main(["report", "demo"])
    report = capsys.readouterr().out
    assert "SCRIPT script.md · 4 sentences · 3 found" in report
    assert "⚠ sentence 3 not found in the recording" in report and "sentence 1: 2 takes" in report
    assert "CUTAWAYS 1 · 1 placed" in report and "board.png" in report
    main(["view", "demo"])
    assert "cutaway [" in capsys.readouterr().out

    main(["render", "demo", "--draft"])
    vdir = next(p for p in d.glob("v1*") if p.is_dir())
    final = next(vdir.glob("*.mp4"))
    summary = json.loads((vdir / "summary.json").read_text(encoding="utf-8"))
    (cut,) = summary["cutaways"]
    assert cut["type"] == "photo" and cut["sentences"] == [2, 2] and summary["cutaway_warnings"] == []
    # two 0.2 s dissolves overlap the shots; each shot starts 0.1 s before its sentence
    before = sum(s["end"] - s["start"] for s in shots[:2]) - 0.4
    total = before + shots[2]["end"] - shots[2]["start"]
    assert cut["start"] == pytest.approx(before + 0.1, abs=0.05) and cut["end"] == pytest.approx(total, abs=0.05)
    top = (0.1, 0.9, 0.1, 0.5)                                # above the subtitles
    assert [tint(final, t, top) for t in (0.5, before - 0.4, (cut["start"] + cut["end"]) / 2)] == ["red", "red", "blue"]
    assert loudness(final, cut["start"] + 0.3, cut["end"] - 0.3) > 0.02     # the voice goes on under the photo
    assert duration(final, "v:0") == pytest.approx(total, abs=0.15)
    assert abs(duration(final, "v:0") - duration(final, "a:0")) < 0.1
    assert len(list((d / "cache" / "voice").glob("*.wav"))) == 1        # the recording's voice cleaned once, whole


@pytest.mark.integration
@requires_ffmpeg
def test_explainer_graphics_over_the_presenter(workspace, monkeypatch):
    pytest.importorskip("skia", reason="the graphics extra (skia-python) is not installed or cannot load", exc_type=ImportError)
    monkeypatch.chdir(workspace.parent)
    script = """How does a message travel?
[bars title="Where it travels" data="Sea:95, Sky:5" unit="%"]
Every time you send one, it splits again.
[lower_third kicker="Source" text="Sample data"]
And they travel apart.
"""
    d = explainer_project(workspace, script, {
        "pip": {"enabled": True},
        "graphics": {"theme": {"colors": {"bg": "#1E1EC8", "grid": "#1E1EC8", "trace": "#1E1EC8"},
                               "decor": {"traces": 0, "chips": 0, "bits": 0}}}})
    from social_video_editor.cli import main

    main(["plan", "demo"])
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    assert [c["template"] for c in plan["cutaways"]] == ["bars", "lower_third"]
    main(["render", "demo", "--draft"])
    vdir = next(p for p in d.glob("v1*") if p.is_dir())
    final = next(vdir.glob("*.mp4"))
    summary = json.loads((vdir / "summary.json").read_text(encoding="utf-8"))
    bars, third = summary["cutaways"]
    assert (bars["overlay"], third["overlay"]) == (False, True)
    assert bars.get("pip") is True and "pip" not in third
    x, y, w, h = pip.layout(540, 960, {"pip": {"enabled": True}})["rect"]     # the draft is half size
    window = ((x + 0.2 * w) / 540, (x + 0.8 * w) / 540, (y + 0.2 * h) / 960, (y + 0.8 * h) / 960)
    assert tint(final, bars["end"] - 0.3, window) == "red"    # the presenter stays in a corner window
    assert tint(final, bars["start"] - 0.2, window) == "red" and tint(final, bars["end"] - 0.3, (0.1, 0.5, 0.5, 0.6)) == "blue"
    corner = (0.02, 0.12, 0.02, 0.06)                         # a corner no graphic draws on
    assert tint(final, bars["end"] - 0.3, corner) == "blue"   # the graphic's stage covers the presenter
    assert tint(final, third["start"] + 1.0, corner) == "red"  # a lower third leaves the presenter in sight
    before, during = frame(final, bars["start"] - 0.2), frame(final, third["start"] + 1.2)
    assert np.abs(during.astype(int) - before.astype(int)).mean() > 0.5       # and draws something over it
    assert summary["sound_effects"] >= 2                      # each graphic brings its sound
    # rendering again reuses every cutaway from the cache
    cached = sorted(p.name for p in (d / "cache" / "segments").glob("cut_*"))
    assert len(cached) == 3 and sorted(Path(n).suffix for n in cached) == [".mkv", ".mov", ".mov"]   # + the window's frame
    main(["render", "demo", "--draft"])
    assert sorted(p.name for p in (d / "cache" / "segments").glob("cut_*")) == cached


@pytest.mark.integration
@requires_ffmpeg
def test_explainer_errors_are_clear(workspace, monkeypatch):
    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, "Nothing of this was said at all today.\n")
    from social_video_editor.cli import main

    with pytest.raises(SystemExit, match="None of the script's sentences"):
        main(["plan", "demo"])
    (d / "script.md").write_text("How does a message travel?\n[photo file=\"missing.png\"]\nAnd they travel apart.\n",
                                 encoding="utf-8")
    main(["plan", "demo", "--force"])
    with pytest.raises(SystemExit, match=r"Cutaway 0 \(photo missing.png\): not a readable photo"):
        main(["render", "demo", "--draft"])
    (d / "script.md").unlink()
    with pytest.raises(SystemExit, match="Missing the script"):
        main(["plan", "demo", "--force"])


@pytest.mark.integration
@requires_ffmpeg
def test_a_clip_without_a_face_has_none(tmp_path):
    """Whatever detector this OpenCV has (none without its model: nothing is downloaded here)."""
    from social_video_editor.adapters.opencv.video_analyzer import OpenCvVideoAnalyzer

    clip = tmp_path / "plain.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x336699:s=180x320:d=3:r=10",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip)], check=True)
    face = OpenCvVideoAnalyzer().face(str(clip))
    assert face is None or (face["ratio"] == 0.0 and face["frames"] == 3 and "x" not in face)
