"""VideoEncoder implemented with FFmpeg."""
from __future__ import annotations

import itertools
import math
import os
import subprocess
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from ...domain.effects import freeze_seconds
from ...domain.models import UserError
from ...log import log
from ..system.hwaccel import (decode_args, device_args, draft_encoder_args, final_decoder, proxy_encoder_args,
                              run_with_fallback, with_upload)
from ..system.tools import FFMPEG, ffmpeg_version
from . import automix_render, filters
from .loudness import ebur128
from .escape import concat_path
from .runner import StderrDrain, popen, run

PROXY_SHORT_SIDE = 540
# long videos (settings render.*): see FfmpegEncoder.compose
CHUNK_DEFAULTS = {"chunk_threshold": 60, "chunk_size": 20, "chunk_crf": 14, "chunk_min_seconds": 300}
CHUNK_DECODER_THREADS = "2"     # per input of a chunk pass: fewer frame buffers per decoder
# Shot segments: FFmpeg 7.0 – 8.0 can deadlock when a frame-threaded decoder holds 5+ frames while the
# audio chain buffers (afftdn) and pads (apad); its default is one thread per core + 1. Fixed in 8.1.
DEADLOCK_VERSIONS = ((7, 0), (8, 1))     # [first affected, first fixed)
SAFE_DECODER_THREADS = "2"
DECODER_THREADS_ENV = "SVE_DECODER_THREADS"
JOBS_ENV = "SVE_JOBS"           # shots rendered at once; overrides render.jobs (see parallel_jobs)
# trial passes of master_audio (gain + limiter → AAC → measure). Very peaky audio needs a 5th pass with
# FFmpeg 7.x, whose AAC encoder overshoots the true peak more than newer versions.
MASTER_PASSES = 6


def segment_decoder_threads(version: tuple[int, int] | None, override: str | None = None) -> str | None:
    """`-threads` for a shot's source input; None = FFmpeg's default (one thread per core + 1).

    `override` ($SVE_DECODER_THREADS): a number of threads (1-64), or "0" / "auto" for FFmpeg's default.
    Otherwise 2 threads on the FFmpeg versions that can deadlock, and when the version is unknown."""
    raw = (override or "").strip().lower()
    if raw:
        if raw in ("0", "auto"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= 64:
            return str(int(raw))
        raise UserError(f"{DECODER_THREADS_ENV} must be a number of threads (1-64), 0 or auto; got {override!r}")
    first, fixed = DEADLOCK_VERSIONS
    if version is None or first <= version < fixed:
        return SAFE_DECODER_THREADS
    return None


def chunk_setting(cfg: dict, key: str):
    v = (cfg.get("render") or {}).get(key)
    return CHUNK_DEFAULTS[key] if v is None else v


def should_chunk(n_segments: int, total: float, cfg: dict) -> bool:
    """Chunked compose: more than render.chunk_threshold shots AND either longer than
    render.chunk_min_seconds or a slideshow recipe (`slideshow: true`)."""
    threshold = int(chunk_setting(cfg, "chunk_threshold"))
    if threshold <= 0 or n_segments <= threshold:
        return False
    return bool(cfg.get("slideshow")) or float(total) > float(chunk_setting(cfg, "chunk_min_seconds"))


_WINDOWS = os.name == "nt"
FRAMES_STALL_SEC = 20.0     # encode_frames: an FFmpeg that printed an error and stalled this long is killed


_TMP_COUNTER = itertools.count()      # next() on a count is atomic in CPython


def _tmp_for(out: Path) -> Path:
    """A temporary name next to `out`, unique per process and call (two shots with the same cache
    signature may render at the same time; thread ids are reused once a thread ends, hence the
    counter), keeping the extension FFmpeg picks the muxer from."""
    return out.with_name(f"{out.stem}.{os.getpid()}-{threading.get_ident()}-{next(_TMP_COUNTER)}.tmp{out.suffix}")


def _publish(tmp: Path, out: Path, attempts: int = 6) -> Path:
    """Atomically moves a finished temporary file to `out` (replacing it). On Windows a file that was
    just closed can stay locked for a moment (antivirus, indexer, a reader of the old `out`), so
    PermissionError is retried there with a short back-off (1.55 s in all)."""
    for k in range(attempts):
        try:
            os.replace(tmp, out)
            return out
        except PermissionError:
            if not _WINDOWS or k == attempts - 1:
                raise
            time.sleep(0.05 * 2 ** k)
    return out


class FfmpegEncoder:
    # ── analysis copies ──────────────────────────────────────────────────────
    def make_proxy(self, info: dict, out: Path, hdr_white: float) -> Path:
        """540-px SDR copy at 30 fps (no audio) to analyze quickly without touching the original."""
        log(f"Proxy of {Path(info['path']).name} ({info['duration']:.0f} s)…")
        if info["width"] <= info["height"]:
            w, h = PROXY_SHORT_SIDE, round(PROXY_SHORT_SIDE * info["height"] / info["width"] / 2) * 2
        else:
            h, w = PROXY_SHORT_SIDE, round(PROXY_SHORT_SIDE * info["width"] / info["height"] / 2) * 2
        vf = f"scale={w}:{h}:flags=bicubic,{filters.sdr_chain(info, hdr_white)},fps=30"
        tmp = _tmp_for(out)
        try:
            run_with_fallback(lambda hw: ["ffmpeg", *device_args(hw), *decode_args(hw), "-i", info["path"],
                                          "-map", "0:v:0", "-vf", with_upload(vf, hw),
                                          *proxy_encoder_args(hw, "3M"), "-an", tmp], run)
            return _publish(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)             # only left behind when FFmpeg failed

    def extract_wav(self, info: dict, out: Path) -> Path:
        run(["ffmpeg", "-i", info["path"], "-map", f"0:{info['audio_index']}", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", out])
        return out

    # ── segments ─────────────────────────────────────────────────────────────
    def encode_shot(self, shot: dict, info: dict, cfg: dict, stats: dict, has_voice: bool,
                    W: int, H: int, draft: bool, out: Path) -> Path:
        """Renders one shot from the ORIGINAL: HDR→SDR, framing, color, speed, zoom, effects, clean audio."""
        fps = int(cfg["output"]["fps"])
        v = float(shot.get("speed", 1.0))
        cfg_shot = cfg if not shot.get("color") else {**cfg, "color": {**cfg["color"], **shot["color"]}}
        # speed + output frame rate FIRST: the frames fps drops (half of a 60 fps source) are never scaled
        # from 4K, tone-mapped, corrected or zoomed (~40 % faster at 60 fps). The same source frames are
        # kept; zoom and flash are evaluated at the output frame's time (identical output for 30 / 60 fps
        # sources at speed 1, a fraction of a frame apart otherwise).
        vf = [f"setpts=(PTS-STARTPTS)/{v:.4f}", f"fps={fps}",
              filters.prescale(info, W, H, cfg, shot),
              filters.sdr_chain(info, float(cfg["color"].get("hdr_white", 0.62))),
              filters.correction_chain(stats, cfg_shot["color"]),
              filters.motion(W, H, cfg, shot, shot["dur"], shot["index"]),
              filters.flash_filters(shot)]
        fz = freeze_seconds(shot)
        if fz > 0:
            vf.append(f"tpad=stop_mode=clone:stop_duration={fz:.3f}")
        vf.append(f"fps={fps},format=yuv420p,setsar=1")
        vf = ",".join(x for x in vf if x)
        af = filters.shot_audio_chain(cfg, shot, has_voice, shot["dur"])
        tmp = _tmp_for(out)
        threads = segment_decoder_threads(ffmpeg_version(), os.environ.get(DECODER_THREADS_ENV))
        threads_args = ["-threads", threads] if threads else []

        def build(hw) -> list:
            # drafts: the hardware backend decodes and encodes; final quality: libx264, VideoToolbox decode only
            dec, enc = (hw, hw) if draft else (final_decoder(hw), None)
            cmd = ["ffmpeg", *device_args(dec), *decode_args(dec), *threads_args,
                   "-ss", f"{shot['start']:.3f}", "-t", f"{shot['end'] - shot['start']:.3f}", "-i", info["path"]]
            if info["has_audio"]:
                audio_map = f"0:{info['audio_index']}"
            else:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                audio_map = "1:a"
            venc = draft_encoder_args(enc) or ["-c:v", "libx264", "-preset", "ultrafast" if draft else "fast",
                                               "-crf", "22" if draft else "12", "-pix_fmt", "yuv420p"]
            return cmd + ["-map", "0:v:0", "-map", audio_map, "-vf", with_upload(vf, enc), "-af", af,
                          "-t", f"{shot['dur']:.4f}", *venc, "-c:a", "pcm_s16le", "-ar", "48000", tmp]

        try:
            run_with_fallback(build, run)
            return _publish(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)             # only left behind when FFmpeg failed

    def encode_frames(self, frames: Iterable[bytes], n: int, W: int, H: int, fps: int, out: Path,
                      audio: dict | None = None, crf: int | None = None) -> Path:
        """Encodes n raw RGB frames (piped to FFmpeg) as a segment like the other shots.

        crf: x264 quality (default 12, the drawn shots'); photos pass render.photo_crf.

        audio=None → silence. Otherwise {"info", "start", "shot", "voice", "cfg", "gain_db", "fade"}:
        the source audio from `start` (source seconds) through the same per-shot chain as a video
        shot (speed, cleanup, compression), `gain_db` lower and faded in/out over `fade` seconds.
        """
        dur = n / fps
        tmp = _tmp_for(out)
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
        info = (audio or {}).get("info") or {}
        if audio and info.get("has_audio"):
            speed = float(audio["shot"].get("speed", 1.0))
            fade = min(float(audio.get("fade", 0.12)), dur / 3)
            af = (filters.shot_audio_chain(audio["cfg"], audio["shot"], bool(audio.get("voice")), dur)
                  + f",volume={float(audio.get('gain_db', -6)):.1f}dB,afade=t=in:d={fade:.3f},"
                  f"afade=t=out:st={max(dur - fade, 0):.4f}:d={fade:.3f}")
            cmd += ["-ss", f"{float(audio['start']):.3f}", "-t", f"{dur * speed + 0.05:.3f}", "-i", info["path"],
                    "-map", "0:v", "-map", f"1:{info['audio_index']}", "-af", af]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a"]
        cmd += ["-frames:v", str(n), "-t", f"{dur:.4f}", "-c:v", "libx264", "-preset", "fast", "-crf", str(12 if crf is None else int(crf)),
                "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", str(tmp)]
        p = popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        # stderr drained in a thread while frames are written: a full stderr pipe (4 KiB on Windows)
        # would block FFmpeg while this loop blocks on a full stdin pipe
        drain = StderrDrain(p.stderr)
        progress, finished = [time.monotonic()], threading.Event()

        def watchdog():
            """FFmpeg 8.1 can stay alive forever, reading nothing, after a fatal error (seen ~2% of the
            time when the encoder fails to open): an error printed + no progress for FRAMES_STALL_SEC
            → kill it, so the error below is raised instead of hanging the render."""
            while not finished.wait(0.5):
                if drain.has_output() and time.monotonic() - progress[0] > FRAMES_STALL_SEC and p.poll() is None:
                    p.kill()
                    return

        threading.Thread(target=watchdog, name="ffmpeg-watchdog", daemon=True).start()
        ok = broken = False
        try:
            for frame in frames:                     # the generator's own errors propagate (FFmpeg killed below)
                try:
                    p.stdin.write(frame)
                except OSError:     # FFmpeg died (BrokenPipeError; EINVAL on Windows): its stderr says why
                    broken = True
                    break
                progress[0] = time.monotonic()
            try:
                p.stdin.close()
            except OSError:
                broken = True
            progress[0] = time.monotonic()
            if p.wait() != 0 or broken:
                raise RuntimeError(f"FFmpeg failed encoding {out.name} (exit {p.returncode}): "
                                   f"{drain.text()[-800:].strip()}")
            ok = True
        finally:
            finished.set()
            if not ok and p.poll() is None:          # e.g. the frame generator raised: stop FFmpeg
                p.kill()
            try:
                p.stdin.close()
            except OSError:                          # flushing what FFmpeg never read
                pass
            p.wait()
            drain.text(timeout=5)                    # the reader thread is done before stderr is closed
            p.stderr.close()
            if not ok:
                tmp.unlink(missing_ok=True)
        return _publish(tmp, out)

    def extract_frame(self, video: Path, out_png: Path, at: float | None = None) -> Path:
        """One frame of an already rendered segment (at=None → the last frame)."""
        if at is None:
            run(["ffmpeg", "-sseof", "-0.12", "-i", video, "-update", "1", "-frames:v", "1", out_png])
        else:
            run(["ffmpeg", "-ss", f"{at:.3f}", "-i", video, "-frames:v", "1", out_png])
        return out_png

    # ── final video ──────────────────────────────────────────────────────────
    def compose(self, segments: list[dict], segment_files: list[Path], layer: Path | None,
                music: Path | None, sfx: Path | None, cfg: dict, W: int, H: int, total: float,
                has_voice: bool, draft: bool, workdir: Path, out: Path, stems_dir: Path | None = None) -> Path:
        """Joins the segments, overlays the text layer and progress bar, mixes music + sfx → premaster.

        stems_dir: also writes stems_dir/{original,music,sfx}.wav (pre-master levels) in the same pass.
        Long videos (should_chunk) are first joined in chunks of render.chunk_size segments. A chunk
        ends where the transition into the next chunk's first shot begins and that chunk starts with
        the same transition (from the tail of the previous segment), so the chunks join with plain
        cuts: the final pass reads them one after another (concat demuxer, one open input), and every
        transition looks the same as in a single graph.
        """
        if cfg.get("slideshow") or should_chunk(len(segments), total, cfg):
            # audio of every segment exactly as long as its picture (else A/V drift over many crossfades)
            segments = [{**s, "exact_audio": True} for s in segments]
        if should_chunk(len(segments), total, cfg):
            listing, files = self._chunks(segments, segment_files, cfg, draft, Path(workdir))
            try:
                return self._compose([{"dur": float(total), "overlap": 0.0, "transition": "cut", "renumber": True,
                                       "exact_audio": True}],
                                     [listing],
                                     layer, music, sfx, cfg, W, H, total, has_voice, draft, workdir, out, stems_dir)
            finally:
                for f in [*files, listing]:
                    Path(f).unlink(missing_ok=True)
        return self._compose(segments, segment_files, layer, music, sfx, cfg, W, H, total,
                             has_voice, draft, workdir, out, stems_dir)

    def _chunks(self, segments: list[dict], files: list[Path], cfg: dict, draft: bool,
                workdir: Path) -> tuple[Path, list[Path]]:
        """Joins groups of segments into chunk files → (ffconcat list of the chunks, chunk files).

        Chunk k covers [start of the transition into its first shot, start of the transition into
        the next chunk's first shot). The transition at the border is rendered inside chunk k from
        the last `overlap` seconds of the previous segment (entry_overlap is at most half of either
        shot, so no other transition overlaps it)."""
        fps = int(cfg["output"]["fps"])
        size = max(int(chunk_setting(cfg, "chunk_size")), 2)
        crf = str(int(chunk_setting(cfg, "chunk_crf")))
        folder = workdir / "_chunks"
        folder.mkdir(parents=True, exist_ok=True)
        n = len(segments)
        eff = [float(s["overlap"]) if i and filters.xfade_name(s["transition"]) and s["overlap"] > 0 else 0.0
               for i, s in enumerate(segments)]
        pos, length = [], 0.0                   # start of every segment in the joined video (as join)
        for i, s in enumerate(segments):
            p = 0.0 if i == 0 else length - eff[i]
            pos.append(p)
            length = p + float(s["dur"])
        starts = list(range(0, n, size))
        if len(starts) > 1 and n - starts[-1] < 2:          # never a one-segment tail chunk
            starts.pop()
        bounds = [(b, starts[k + 1] if k + 1 < len(starts) else n) for k, b in enumerate(starts)]
        out_files, lines = [], ["ffconcat version 1.0"]
        for k, (b, e) in enumerate(bounds):
            end = pos[e] if e < n else length
            frames = max(int(round((end - pos[b]) * fps)), 1)
            segs, inputs = [], []
            if b and eff[b] > 0:                # the border transition: tail of the previous segment
                prev = float(segments[b - 1]["dur"])
                segs.append({"dur": eff[b], "overlap": 0.0, "transition": "cut", "skip": prev - eff[b],
                             "exact_audio": True})
                inputs += ["-threads", CHUNK_DECODER_THREADS, "-i", files[b - 1]]
            for i in range(b, e):
                seg = dict(segments[i])
                if i == b and not segs:
                    seg.update(overlap=0.0, transition="cut")
                if i == e - 1:          # the chunk's graph ends at its border by itself (see the run below)
                    cut = float(seg["dur"]) - (eff[e] if e < n else 0.0)
                    seg.update(dur=cut, end=cut)
                segs.append(seg)
                inputs += ["-threads", CHUNK_DECODER_THREADS, "-i", files[i]]
            dest = folder / f"chunk_{k:03d}.mkv"
            log(f"  chunk {k + 1}/{len(bounds)}: shots {b + 1}–{e} ({frames / fps:.1f} s)")
            joined, v, a = filters.join(segs, fps)
            # The graph ends at the chunk border by itself: its last input is cut there ("end" above), so
            # the chunk has exactly `frames` frames and round(frames / fps * 48000) samples. Nothing may
            # close an output while the inputs still hold data: FFmpeg 7.x (seen with 7.1.1; not with
            # 8.0.1 / 8.1.2) then sometimes aborts with "Assertion best_input >= 0 failed" (choose_input in
            # fftools/ffmpeg_filter.c). The old output `-t` stopped every non-final chunk ~0.1 s before its
            # graph's end and aborted ~2% of the runs of a 21-input chunk under CPU load; a trim at the end
            # of the graph did the same. No -frames:v either: with it FFmpeg 7.1 cuts the audio to the
            # start of the last video frame (16 ms short). The trims below are only a cap: they never cut a
            # graph of the planned length.
            samples = int(round(frames / fps * 48000))
            joined += f";{v}trim=end_frame={frames}[vchunk];{a}atrim=end_sample={samples}[achunk]"
            script = folder / f"chunk_{k:03d}.txt"
            script.write_text(joined, encoding="utf-8", newline="\n")
            run(["ffmpeg", *inputs, "-/filter_complex", script, "-map", "[vchunk]", "-map", "[achunk]",
                 "-r", str(fps), "-c:v", "libx264", "-preset", "ultrafast" if draft else "fast", "-crf", crf,
                 "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", dest])
            script.unlink(missing_ok=True)
            out_files.append(dest)
            lines += [f"file {concat_path(dest.name)}", f"duration {frames / fps:.6f}"]
        listing = folder / "chunks.ffconcat"
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return listing, out_files

    def _compose(self, segments: list[dict], segment_files: list[Path], layer: Path | None,
                 music: Path | None, sfx: Path | None, cfg: dict, W: int, H: int, total: float,
                 has_voice: bool, draft: bool, workdir: Path, out: Path, stems_dir: Path | None = None) -> Path:
        fps = int(cfg["output"]["fps"])
        inputs = []
        for s in segment_files:
            if Path(s).suffix == ".ffconcat":                # chunked: the chunks one after another
                inputs += ["-f", "concat", "-safe", "0", "-i", s]
            else:
                inputs += ["-i", s]
        idx = len(segment_files)
        idx_layer = idx_music = idx_sfx = None
        if layer:
            inputs += ["-f", "concat", "-safe", "0", "-i", layer]
            idx_layer, idx = idx, idx + 1
        if music:
            inputs += ["-i", music]
            idx_music, idx = idx, idx + 1
        if sfx:
            inputs += ["-i", sfx]
            idx_sfx, idx = idx, idx + 1

        joined, v, a = filters.join(segments, fps)
        parts = [joined]
        if idx_layer is not None:
            parts.append(f"[{idx_layer}:v]fps={fps},format=rgba,setpts=PTS-STARTPTS[txt];"
                         f"{v}[txt]overlay=0:0:format=auto:eof_action=pass:shortest=0[vt]")
            v = "[vt]"
        if cfg["effects"].get("progress_bar"):
            bar_h = max(int(H * 0.006), 4)
            col = cfg["effects"].get("progress_bar_color", "#FFFFFF").lstrip("#")
            parts.append(f"color=c=0x{col}:s={W}x{bar_h}:r={fps}:d={total:.3f}[bar];"
                         f"{v}[bar]overlay=x='-w+w*t/{total:.3f}':y=0[vb]")
            v = "[vb]"
        fades = filters.output_fades(cfg, total)       # output.fade_in / fade_out ("" by default)
        parts.append(f"{v}{fades + ',' if fades else ''}format=yuv420p,setsar=1[vout]")
        stems = {} if stems_dir else None
        mix, am = filters.final_mix(a, total, cfg, idx_music, idx_sfx, has_voice, stems)
        parts.append(mix)
        script = workdir / "_filter.txt"
        script.write_text(";\n".join(parts), encoding="utf-8", newline="\n")
        extra = []
        if stems:
            Path(stems_dir).mkdir(parents=True, exist_ok=True)
            for name, label in stems.items():
                extra += ["-map", label, "-t", f"{total:.4f}", "-c:a", "pcm_s16le", "-ar", "48000",
                          Path(stems_dir) / f"{name}.wav"]
        run(["ffmpeg", *inputs, "-/filter_complex", script, "-map", "[vout]", "-map", am,
             "-t", f"{total:.4f}", "-r", str(fps), *filters.video_output_options(cfg, draft),
             "-c:a", "pcm_s16le", "-ar", "48000", out, *extra])
        return out

    # ── AutoMix (playlist → one continuous mix) ─────────────────────────────
    def music_features(self, path: Path, analysis: dict, cache_dir: Path) -> dict:
        """Loudness, level envelope, onset rate and beat accents of a song (cached in cache_dir)."""
        return automix_render.features(Path(path), analysis, Path(cache_dir))

    def render_automix(self, paths: list[Path], plan: dict, cache_dir: Path) -> Path:
        """The continuous mix of a playlist (domain.automix.plan_mix plan) as one WAV (cached)."""
        return automix_render.render_cached([Path(p) for p in paths], plan, Path(cache_dir))

    def measure_loudness(self, path: Path, cfg: dict | None = None) -> dict:
        """{"integrated" LUFS, "true_peak" dBTP, "lra" LU}: the same EBU R128 measurement as `sve check`."""
        return ebur128(path)

    def _limited(self, original: Path, gain: float, ceiling_db: float, out: Path) -> dict:
        """gain + 4x-oversampled limiter (controls the true peak) → out; returns its measurement."""
        run(["ffmpeg", "-i", original, "-af",
             f"volume={gain:.2f}dB,aresample=192000,"
             f"alimiter=limit={10 ** (ceiling_db / 20):.5f}:attack=1:release=60:level=0,aresample=48000",
             "-c:a", "pcm_s24le", out])
        return ebur128(out)

    def _aac_only(self, audio: Path, cfg: dict, out: Path) -> dict:
        run(["ffmpeg", "-i", audio, "-c:a", "aac", "-b:a", f"{int(cfg['export']['audio_kbps'])}k", "-ar", "48000",
             out])
        return ebur128(out)

    def _converge(self, original: Path, gain: float, aim: float, ceiling: float, max_gain: float,
                  work: Path) -> tuple[float, float]:
        """Secant search of the gain whose limited output measures `aim` LUFS (±0.3) → (gain, measured).
        The limiter "eats" loudness, so 1 dB of gain gives less than 1 dB once it limits."""
        pts: list[tuple[float, float]] = []
        for _ in range(8):
            measured = self._limited(original, gain, ceiling, work)["integrated"]
            pts.append((gain, measured))
            if abs(measured - aim) < 0.3:
                break
            slope = 1.0
            if len(pts) >= 2 and abs(pts[-1][0] - pts[-2][0]) > 1e-3:
                slope = min(max((pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0]), 0.15), 1.0)
            new = min(gain + (aim - measured) / slope, max_gain)
            if abs(new - gain) < 0.01:               # stuck at the gain cap
                break
            gain = new
        best = min(pts, key=lambda p: abs(p[1] - aim))
        if best[0] != pts[-1][0]:
            self._limited(original, best[0], ceiling, work)
        return best

    def master_audio(self, src: Path, out: Path, cfg: dict) -> dict:
        """Brings the audio to the target integrated loudness (−14 LUFS) with the true peak at most
        audio.true_peak (−1 dBTP). The video stream is copied.

        Measured with EBU R128 (ebur128, the same measurement as `sve check`). Gain + 4x-oversampled
        limiter, ALWAYS applied, the gain capped at audio.master_max_gain_db. Then the AAC file is
        measured: if its true peak is above the target the limiter ceiling is lowered, if its loudness
        drifted the aim is corrected, and the pass is repeated. Every pass encodes the AAC alone; the
        video is muxed (copied) once, with the final AAC, at the end. Returns
        {"before_lufs", "after_lufs", "true_peak_dbtp", "gain_db", "converged"[, "warning"]}.
        """
        a = cfg["audio"]
        target, peak = float(a["loudness"]), float(a["true_peak"])
        max_gain = float(a.get("master_max_gain_db", 24))
        before = ebur128(src)["integrated"]
        if not math.isfinite(before) or before <= -69:
            log("Warning: the audio is silent or could not be measured; copying without mastering.")
            run(["ffmpeg", "-i", src, "-c", "copy", out])
            return {"before_lufs": None, "after_lufs": None, "true_peak_dbtp": None, "converged": False,
                    "warning": "silent or unmeasurable audio: not mastered"}
        original = src.with_name(src.stem + "_audio.wav")
        work = src.with_name(src.stem + "_master.wav")
        aac = src.with_name(src.stem + "_master.m4a")
        try:
            run(["ffmpeg", "-i", src, "-map", "0:a", "-c:a", "pcm_s24le", original])

            def encode() -> dict:
                return self._aac_only(work, cfg, aac)

            ceiling, aim = peak - 1.2, target        # margin for the true peak after AAC encoding
            gain = min(target - before, max_gain)
            for _ in range(MASTER_PASSES):
                gain, _measured = self._converge(original, gain, aim, ceiling, max_gain, work)
                final = encode()
                tp_ok = final["true_peak"] <= peak
                if not tp_ok:                        # AAC overshoot: more headroom
                    ceiling -= final["true_peak"] - peak + 0.5
                drift = target - final["integrated"]
                if tp_ok and abs(drift) < 0.3:
                    break
                aim += max(min(drift, 3.0), -3.0)    # AAC changed the loudness: aim higher/lower
            if final["true_peak"] > peak:            # guarantee: never deliver above the ceiling
                ceiling -= final["true_peak"] - peak + 3.0
                self._limited(original, gain, ceiling, work)
                final = encode()
            run(["ffmpeg", "-i", src, "-i", aac, "-map", "0:v", "-map", "1:a", "-c", "copy",
                 "-movflags", "+faststart", out])
        finally:
            original.unlink(missing_ok=True)
            work.unlink(missing_ok=True)
            aac.unlink(missing_ok=True)
        res = {"before_lufs": round(before, 2), "after_lufs": round(final["integrated"], 2),
               "true_peak_dbtp": round(final["true_peak"], 2), "gain_db": round(gain, 2)}
        why = []
        if abs(final["integrated"] - target) > 0.5:
            why.append(f"{final['integrated']:.1f} LUFS instead of {target:g}"
                       + (f" (gain capped at +{max_gain:g} dB)" if gain >= max_gain - 0.01 else
                          " (very peaky audio: the true-peak limiter cannot raise it further)"))
        if final["true_peak"] > peak:
            why.append(f"true peak {final['true_peak']:+.1f} dBTP above {peak:+g}")
        res["converged"] = not why
        if why:
            res["warning"] = "mastering did not converge: " + "; ".join(why)
            log(f"Warning: {res['warning']}")
        return res

    def cover(self, video: Path, t: float, out: Path) -> Path:
        run(["ffmpeg", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "2", out])
        return out

    # ── parallel shot rendering ──────────────────────────────────────────────
    def parallel_jobs(self, cfg: dict) -> int:
        """Shots to encode at once: $SVE_JOBS, else render.jobs (auto = from the CPUs this process may use)."""
        from ...domain.config import resolve_jobs      # imported here: keeps this addition self-contained

        return resolve_jobs((cfg.get("render") or {}).get("jobs"), os.environ.get(JOBS_ENV),
                            self.usable_cpus())

    @staticmethod
    def usable_cpus() -> int:
        """CPUs this process may run on: os.process_cpu_count() (Python 3.13+, honors affinity and
        PYTHON_CPU_COUNT), else the scheduler affinity (Linux), else os.cpu_count()."""
        if hasattr(os, "process_cpu_count"):
            return os.process_cpu_count() or 1
        if hasattr(os, "sched_getaffinity"):
            try:
                return len(os.sched_getaffinity(0)) or 1
            except OSError:
                pass
        return os.cpu_count() or 1
