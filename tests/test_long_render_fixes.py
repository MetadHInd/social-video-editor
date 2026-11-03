"""Long slideshow renders: chunk gating, memory / disk savings, output fades and their checks."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg import filters
from social_video_editor.adapters.ffmpeg.encoder import FfmpegEncoder, should_chunk
from social_video_editor.adapters.ffmpeg.inspector import FfmpegRenderInspector
from social_video_editor.adapters.system.tools import FFMPEG
from social_video_editor.application.check import Checker
from social_video_editor.application.render import stems_wanted
from social_video_editor.domain import check as ck
from social_video_editor.domain.slideshow_timing import fit_photos_to_music
from test_long_compose import FPS, tiny_segments, timeline

SR = 48000


# ── 3. chunk gating ──────────────────────────────────────────────────────────

def test_chunking_needs_many_shots_and_a_long_video_or_a_slideshow(cfg):
    assert not should_chunk(61, 120.0, cfg)                 # many short shots, short video: one graph
    assert should_chunk(61, 301.0, cfg)                     # long video
    assert not should_chunk(60, 900.0, cfg)                 # few shots
    assert should_chunk(61, 120.0, {**cfg, "slideshow": True})
    assert not should_chunk(30, 120.0, {**cfg, "slideshow": True})
    assert should_chunk(61, 120.0, {**cfg, "render": {"chunk_min_seconds": 100}})
    assert not should_chunk(500, 3600.0, {**cfg, "render": {"chunk_threshold": 0}})


# ── 1b. no aloop for a playlist mix that covers the video ────────────────────

def _music_chain(cfg: dict, total: float) -> str:
    mix, _ = filters.final_mix("[a]", total, cfg, 1, None, False)
    return mix


def test_music_is_looped_except_for_a_playlist_mix_long_enough(cfg):
    single = {**cfg, "music": {**cfg["music"], "_start": 0}}
    assert "aloop=" in _music_chain(single, 3600.0)               # single track: unchanged chain
    mix = {**cfg, "music": {**cfg["music"], "_start": 0, "_automix": {"duration": 3600.0}}}
    chain = _music_chain(mix, 3599.9)
    assert "aloop=" not in chain and "atrim=0:3599.9000" in chain
    short_mix = {**cfg, "music": {**cfg["music"], "_start": 0, "_automix": {"duration": 100.0}}}
    assert "aloop=" in _music_chain(short_mix, 200.0)             # a mix shorter than the video still loops


# ── 2. stems ─────────────────────────────────────────────────────────────────

def _tl(*windows):
    return [SimpleNamespace(piece=SimpleNamespace(source_window=lambda w=w: w)) for w in windows]


def test_stems_only_for_short_renders_with_original_sound(cfg):
    video = _tl((0, 1.0, 2.0, 1.0), None)
    photos = _tl(None, None)
    assert stems_wanted(cfg, video, 30.0)                         # short vertical video: as before
    assert not stems_wanted(cfg, video, 601.0)                    # long render
    assert not stems_wanted(cfg, photos, 30.0)                    # photos only: no original sound
    forced = {**cfg, "export": {**cfg["export"], "stems": True}}
    assert stems_wanted(forced, photos, 3600.0)
    off = {**cfg, "export": {**cfg["export"], "stems": False}}
    assert not stems_wanted(off, video, 30.0)


# ── 1a. chunk borders: same picture as one graph ─────────────────────────────

def _gray_frames(path: Path) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
                          "-vf", "scale=80:46", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 46, 80).astype(np.float32)


@requires_ffmpeg
def test_chunked_compose_matches_the_single_graph(tmp_path, cfg):
    n = 64
    files = tiny_segments(tmp_path / "segs", n)
    segs, total = timeline(n)
    base = {**cfg, "output": {**cfg["output"], "fps": FPS}}
    # chunks of 21: every border (21, 42, 63) is a fade, rendered from the previous segment's tail
    chunked_cfg = {**base, "render": {"chunk_threshold": 60, "chunk_size": 21, "chunk_min_seconds": 0}}
    enc = FfmpegEncoder()
    chunked = enc.compose(segs, files, None, None, None, chunked_cfg, 160, 90, total, False, True,
                          tmp_path, tmp_path / "chunked.mkv")
    assert not list((tmp_path / "_chunks").glob("*"))            # chunks and list cleaned
    single = enc.compose(segs, files, None, None, None, {**base, "render": {"chunk_threshold": 0}}, 160, 90,
                         total, False, True, tmp_path, tmp_path / "single.mkv")
    a, b = _gray_frames(chunked), _gray_frames(single)
    assert len(a) == len(b) == round(total * FPS)
    # one frame of slack: the single 64-input graph itself places some transitions a frame off
    # (float offsets); a border rendered as a hard cut instead of the fade differs by ≈ 8.5
    padded = np.concatenate([b[:1], b, b[-1:]])
    diff = np.min([np.abs(a - padded[k:k + len(a)]).mean(axis=(1, 2)) for k in range(3)], axis=0)
    assert diff.max() < 5.5, int(diff.argmax())
    assert diff.mean() < 2.5


def test_join_can_skip_the_start_of_an_input_and_renumber():
    joined, _, _ = filters.join([{"dur": 0.1, "overlap": 0.0, "transition": "cut", "skip": 0.4},
                                 {"dur": 0.5, "overlap": 0.1, "transition": "fade"}], 30)
    assert "[0:v]trim=start=0.383333,setpts=PTS-STARTPTS,fps=30" in joined and "[0:a]atrim=start=0.400000,asetpts" in joined
    assert "xfade=transition=fade:duration=0.1000:offset=0.0000" in joined
    plain, _, _ = filters.join([{"dur": 1.0, "overlap": 0.0, "transition": "cut", "renumber": True}], 30)
    assert plain.startswith("[0:v]setpts=N/(30*TB),fps=30") and "[0:a]asetpts=N/SR/TB,asetpts" in plain


@requires_ffmpeg
def test_chunked_compose_reads_the_chunks_through_one_input(tmp_path, cfg, monkeypatch):
    n = 64
    files = tiny_segments(tmp_path / "segs", n)
    segs, total = timeline(n)
    seen = {}
    real = FfmpegEncoder._compose

    def spy(self, segments, segment_files, *args, **kw):
        seen["segments"], seen["files"] = [dict(x) for x in segments], list(segment_files)
        seen["listing"] = Path(segment_files[0]).read_text()
        return real(self, segments, segment_files, *args, **kw)

    monkeypatch.setattr(FfmpegEncoder, "_compose", spy)
    cfg = {**cfg, "output": {**cfg["output"], "fps": FPS}, "slideshow": True}   # defaults: chunks of 20
    FfmpegEncoder().compose(segs, files, None, None, None, cfg, 160, 90, total, False, True,
                            tmp_path, tmp_path / "out.mkv")
    assert len(seen["files"]) == 1 and Path(seen["files"][0]).suffix == ".ffconcat"
    durations = [float(x.split()[1]) for x in seen["listing"].splitlines() if x.startswith("duration")]
    assert len(durations) == 4                                    # 64 shots: 20 + 20 + 20 + 4
    assert sum(durations) == pytest.approx(total, abs=1e-3)


# ── 4. output fades ──────────────────────────────────────────────────────────

def _luma_at(path: Path, frame: int | str) -> float:
    vf = "select=eq(n\\,{})".format(frame) if isinstance(frame, int) else "reverse"
    args = [FFMPEG, "-hide_banner", "-loglevel", "error"]
    if frame == "last":
        args += ["-sseof", "-0.2"]
        vf = "scale=32:18"
    args += ["-i", str(path), "-map", "0:v:0", "-vf", vf if frame == "last" else f"{vf},scale=32:18",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(args, capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 18, 32)
    return float(frames[-1 if frame == "last" else 0].mean())


@requires_ffmpeg
@pytest.mark.parametrize("chunked", [False, True])
def test_output_fades_from_and_to_black(tmp_path, cfg, chunked):
    n = 64 if chunked else 8
    files = tiny_segments(tmp_path / "segs", n)
    segs, total = timeline(n)
    out_cfg = {**cfg["output"], "fps": FPS, "fade_in": 0.5, "fade_out": 1.0}
    c = {**cfg, "output": out_cfg, "slideshow": chunked}
    out = FfmpegEncoder().compose(segs, files, None, None, None, c, 160, 90, total, False, True,
                                  tmp_path, tmp_path / "faded.mkv")
    assert _luma_at(out, 0) < 20                                  # first frame black (limited range: 16)
    assert _luma_at(out, int(0.5 * FPS) + 1) > 40                 # picture once the fade-in is over
    assert _luma_at(out, round((total - 1.5) * FPS)) > 40         # not dimmed before the fade-out
    assert _luma_at(out, "last") < 20                             # last frame black, at the very end


def test_fades_filter_off_by_default(cfg):
    assert filters.output_fades(cfg, 30.0) == ""
    f = filters.output_fades({**cfg, "output": {**cfg["output"], "fade_in": 1.5, "fade_out": 3.0}}, 100.0)
    assert f == f"fade=t=in:st=0:d=1.5000,fade=t=out:st=97.0000:d={3 - 1 / 30:.4f}"


# ── 4. `sve check` ignores the intended fades ────────────────────────────────

def test_black_frames_inside_the_output_fades_are_not_reported():
    out = {"fade_in": 1.5, "fade_out": 3.0}
    black = [[0.0, 0.4], [10.0, 10.5], [98.2, 100.0]]
    assert ck.outside_output_fades(black, out, 100.0, 0.1) == [[10.0, 10.5]]
    assert ck.outside_output_fades([[0.0, 4.0]], out, 100.0, 0.1) == [[1.55, 4.0]]   # longer than the fade
    assert ck.outside_output_fades(black, {}, 100.0, 0.1) == black                    # no fades: unchanged


def test_checker_video_skips_intended_fades(cfg):
    events = {"black": [[0.0, 0.3], [50.0, 50.4], [98.5, 100.0]], "freeze": []}
    svc = SimpleNamespace(inspector=SimpleNamespace(video_events=lambda *a: events))
    version = {"config": {**cfg, "output": {**cfg["output"], "fade_in": 1.5, "fade_out": 3.0}},
               "summary": {"duration_sec": 100.0, "shot_details": []}, "plan": {"shots": []}, "stems": {}}
    c = Checker(svc, version, ck.thresholds())
    c.video(Path("x.mp4"))
    assert c.metrics["video"]["black"] == [[50.0, 50.4]]
    assert c.errors == ["black frames 50.00–50.40 s"]


# ── 1c. block-wise audio analysis ────────────────────────────────────────────

def _synthetic_stereo(seconds: float = 7.3) -> np.ndarray:
    rng = np.random.default_rng(3)
    t = np.arange(int(SR * seconds)) / SR
    left = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.05 * rng.standard_normal(len(t))
    right = 0.3 * np.sin(2 * np.pi * 3100 * t) * (t > 3)
    x = np.stack([left, right], axis=1).astype(np.float32)
    x[int(1.0 * SR):int(2.2 * SR)] = 0                            # a silence
    x[int(5.0 * SR):int(5.0 * SR) + 4, 1] = 1.0                   # a clipped run
    x[int(2.2 * SR) - 1:int(2.2 * SR) + 2, 0] = -1.0              # another one across a block border
    return x


@pytest.mark.parametrize("block", [SR // 3 + 7, int(2.2 * SR), 10 * SR])
def test_block_wise_audio_analysis_equals_the_whole_track(block):
    x = _synthetic_stereo()
    mono = x.mean(axis=1)
    scan = ck.AudioScan(SR, keep=[(int(2.5 * SR), int(4.5 * SR))])
    for i in range(0, len(x), block):
        scan.feed(x[i:i + block])
    assert scan.clipping() == ck.clipping(np.abs(x).max(axis=1), SR)
    assert scan.silences(-50, 0.5) == ck.silences(mono, SR, -50, 0.5)
    h, ref = scan.harshness(), ck.harshness(mono, SR)
    assert h["worst_t"] == ref["worst_t"]
    assert h["ratio"] == pytest.approx(ref["ratio"], abs=1e-3)
    assert h["worst_ratio"] == pytest.approx(ref["worst_ratio"], abs=1e-6)
    assert np.array_equal(scan.excerpt(int(3 * SR), int(4 * SR)), x[int(3 * SR):int(4 * SR)])
    assert scan.seconds == pytest.approx(len(x) / SR)


def test_block_wise_analysis_of_a_very_short_track():
    x = _synthetic_stereo(0.05)                                   # shorter than one FFT and one window
    scan = ck.AudioScan(SR)
    scan.feed(x)
    mono = x.mean(axis=1)
    h, ref = scan.harshness(), ck.harshness(mono, SR)
    assert h["ratio"] == pytest.approx(ref["ratio"], abs=1e-6) and h["worst_t"] == ref["worst_t"]


@requires_ffmpeg
def test_inspector_streams_the_same_audio_in_blocks(tmp_path):
    wav = tmp_path / "a.wav"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "aevalsrc='0.2*sin(2*PI*330*t)|0.1*sin(2*PI*550*t)':d=2.3:s=48000", str(wav)], check=True)
    insp = FfmpegRenderInspector()
    whole = insp.audio(wav)
    blocks = list(insp.audio_blocks(wav, SR, 0.5))
    assert len(blocks) == 5 and np.array_equal(np.concatenate(blocks), whole)


# ── 5. the last photo holds through the fade-out ─────────────────────────────

def test_last_photo_holds_through_the_fade_out():
    L = 60.0
    beats = [round(0.5 * k, 3) for k in range(1, int(L / 0.5))]
    tl = {"duration": L, "songs": [{"energy": 0.9}], "boundaries": [], "beats": beats, "downbeats": beats[3::4]}
    free = fit_photos_to_music(16, tl, 3.0, 5.5, 1.0)
    held = fit_photos_to_music(16, tl, 3.0, 5.5, 1.0, hold_last=4.5)
    assert sum(held) == pytest.approx(L) and len(held) == 16
    assert held[-1] >= 4.5 - 1e-6
    assert free[-1] < 4.5                                         # without the hold the last slot is shorter


# ── audio exactly as long as the video (long / chunked path) ────────────────

def _stream_seconds(path: Path) -> dict:
    out = {}
    for kind in ("v", "a"):
        raw = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", f"0:{kind}:0",
                              "-f", "null", "-"], capture_output=True, text=True)
        assert raw.returncode == 0
    from social_video_editor.adapters.system.tools import FFPROBE
    for kind in ("v", "a"):
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", f"{kind}:0", "-show_entries",
                            "packet=pts_time,duration_time", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, check=True).stdout.split()
        ends = [float(a) + float(b) for a, b in (x.split(",")[:2] for x in r if "," in x)]
        out[kind] = max(ends)
    return out


@requires_ffmpeg
def test_chunked_slideshow_audio_lasts_as_long_as_the_video(tmp_path, cfg):
    """Photo-like segments whose PCM audio stops at a whole codec frame (≈ 19 ms short each)."""
    enc = FfmpegEncoder()
    n, frames = 64, 14
    files = [enc.encode_frames((bytes(32 * 18 * 3) for _ in range(frames)), frames, 32, 18, FPS,
                               tmp_path / f"p{i:02d}.mkv") for i in range(n)]
    segs, total = [], 0.0
    for i in range(n):
        ov = 0.1 if i else 0.0
        segs.append({"dur": frames / FPS, "overlap": ov, "transition": "fade" if i else "cut"})
        total += frames / FPS - ov
    c = {**cfg, "output": {**cfg["output"], "fps": FPS}, "slideshow": True,
         "render": {"long_master_sec": 1}}
    music = tmp_path / "mix.wav"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='0.1*sin(2*PI*440*t)|0.1*sin(2*PI*660*t)':d={total + 0.2:.3f}:s=48000", str(music)],
                   check=True)
    c["music"] = {**c["music"], "_start": 0, "_automix": {"duration": total + 0.2}}
    pre = enc.compose(segs, files, None, music, None, c, 32, 18, total, False, True, tmp_path, tmp_path / "pre.mkv")
    final = tmp_path / "final.mp4"
    enc.master_audio(pre, final, c)
    for path in (pre, final):
        d = _stream_seconds(path)
        assert d["v"] == pytest.approx(total, abs=1 / FPS), path.name
        assert d["a"] == pytest.approx(d["v"], abs=1 / FPS), path.name
