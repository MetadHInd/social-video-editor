"""FFmpeg 7.x scheduler abort ("Assertion best_input >= 0 failed"): chunk graphs end by themselves at the
chunk border (nothing closes an output early), and the runner retries the rare abort."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg import filters, runner
from social_video_editor.adapters.ffmpeg.encoder import FfmpegEncoder
from social_video_editor.adapters.system.tools import FFMPEG, FFPROBE
from test_long_compose import FPS, timeline, tiny_segments


def _fake_calls(monkeypatch, results):
    """runner.call returns the given (returncode, stderr) pairs in order; returns the list of calls."""
    seen = []

    def fake(cmd, **kwargs):
        seen.append(cmd)
        code, err = results[min(len(seen), len(results)) - 1]
        return SimpleNamespace(returncode=code, stderr=err, stdout="")

    monkeypatch.setattr(runner, "call", fake)
    return seen


def test_runner_retries_the_ffmpeg7_scheduler_abort(monkeypatch):
    abort = (-6, "Assertion best_input >= 0 failed at src/fftools/ffmpeg_filter.c:2122")
    seen = _fake_calls(monkeypatch, [abort, (0, "")])
    assert runner.run(["ffmpeg", "-i", "x"]).returncode == 0 and len(seen) == 2
    seen = _fake_calls(monkeypatch, [abort, abort, abort, abort])
    with pytest.raises(RuntimeError):
        runner.run(["ffmpeg", "-i", "x"])
    assert len(seen) == 1 + runner.SCHEDULER_RETRIES                 # bounded
    seen = _fake_calls(monkeypatch, [(1, "Invalid argument"), (0, "")])
    with pytest.raises(RuntimeError):
        runner.run(["ffmpeg", "-i", "x"])
    assert len(seen) == 1                                             # other errors are never retried


def test_join_cuts_an_input_at_its_end():
    segs = [{"dur": 0.5, "overlap": 0.0, "transition": "cut"},
            {"dur": 0.4, "overlap": 0.1, "transition": "fade", "end": 0.4}]
    joined, _, _ = filters.join(segs, 30)
    assert "[1:v]setpts=PTS-STARTPTS,fps=30,trim=end_frame=12,settb=AVTB[sv1]" in joined
    assert "[1:a]asetpts=PTS-STARTPTS,atrim=end_sample=19200[sa1]" in joined


def _counts(path) -> tuple[int, int]:
    """(video frames, audio samples at 48 kHz) of a rendered chunk."""
    frames = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                             "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True, check=True).stdout.strip()
    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a",
                          "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], capture_output=True, check=True).stdout
    return int(frames), len(pcm) // 2


@pytest.mark.integration
@requires_ffmpeg
def test_a_non_final_chunk_is_exactly_as_long_as_planned(tmp_path, cfg):
    n = 24
    files = tiny_segments(tmp_path / "segs", n)
    segs, _ = timeline(n)
    c = {**cfg, "output": {**cfg["output"], "fps": FPS},
         "render": {"chunk_threshold": 2, "chunk_size": 12, "chunk_min_seconds": 0}}
    _, chunks = FfmpegEncoder()._chunks(segs, files, c, False, tmp_path)
    listing = (tmp_path / "_chunks" / "chunks.ffconcat").read_text(encoding="utf-8").split()
    first = float(listing[listing.index("duration") + 1])
    frames = int(round(first * FPS))
    assert _counts(chunks[0]) == (frames, int(round(frames / FPS * 48000)))
