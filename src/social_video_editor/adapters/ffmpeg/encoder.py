"""VideoEncoder implemented with FFmpeg."""
from __future__ import annotations

import math
import subprocess
from collections.abc import Iterable
from pathlib import Path

from ...domain.effects import freeze_seconds
from ...log import log
from ..system.hwaccel import decode_args, proxy_encoder_args, run_with_fallback
from ..system.tools import FFMPEG
from . import filters
from .loudness import ebur128
from .runner import run

PROXY_SHORT_SIDE = 540


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
        tmp = out.with_suffix(".tmp.mp4")
        run_with_fallback(lambda hw: ["ffmpeg", *decode_args(hw), "-i", info["path"], "-map", "0:v:0", "-vf", vf,
                                      *proxy_encoder_args(hw, "3M"), "-an", tmp], run)
        tmp.rename(out)
        return out

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
        vf = [filters.prescale(info, W, H, cfg, shot),
              filters.sdr_chain(info, float(cfg["color"].get("hdr_white", 0.62))),
              filters.correction_chain(stats, cfg_shot["color"]),
              f"setpts=(PTS-STARTPTS)/{v:.4f}",
              filters.motion(W, H, cfg, shot, shot["dur"], shot["index"]),
              filters.flash_filters(shot)]
        fz = freeze_seconds(shot)
        if fz > 0:
            vf.append(f"tpad=stop_mode=clone:stop_duration={fz:.3f}")
        vf.append(f"fps={fps},format=yuv420p,setsar=1")
        vf = ",".join(x for x in vf if x)
        af = filters.shot_audio_chain(cfg, shot, has_voice, shot["dur"])
        tmp = out.with_suffix(".tmp.mkv")

        def build(hw: bool) -> list:
            cmd = ["ffmpeg", *decode_args(hw), "-ss", f"{shot['start']:.3f}", "-t", f"{shot['end'] - shot['start']:.3f}",
                   "-i", info["path"]]
            if info["has_audio"]:
                audio_map = f"0:{info['audio_index']}"
            else:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                audio_map = "1:a"
            return cmd + ["-map", "0:v:0", "-map", audio_map, "-vf", vf, "-af", af, "-t", f"{shot['dur']:.4f}",
                          "-c:v", "libx264", "-preset", "ultrafast" if draft else "fast",
                          "-crf", "22" if draft else "12", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
                          "-ar", "48000", tmp]

        run_with_fallback(build, run)
        tmp.rename(out)
        return out

    def encode_frames(self, frames: Iterable[bytes], n: int, W: int, H: int, fps: int, out: Path,
                      audio: dict | None = None) -> Path:
        """Encodes n raw RGB frames (piped to FFmpeg) as a segment like the other shots.

        audio=None → silence. Otherwise {"info", "start", "shot", "voice", "cfg", "gain_db", "fade"}:
        the source audio from `start` (source seconds) through the same per-shot chain as a video
        shot (speed, cleanup, compression), `gain_db` lower and faded in/out over `fade` seconds.
        """
        dur = n / fps
        tmp = out.with_suffix(".tmp.mkv")
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
        cmd += ["-frames:v", str(n), "-t", f"{dur:.4f}", "-c:v", "libx264", "-preset", "fast", "-crf", "12",
                "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", str(tmp)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        ok = False
        try:
            try:
                for frame in frames:
                    p.stdin.write(frame)
                p.stdin.close()
            except BrokenPipeError:                  # FFmpeg died: its stderr says why (raised below)
                pass
            err = p.stderr.read()
            if p.wait() != 0:
                raise RuntimeError(f"FFmpeg failed encoding {out.name} (exit {p.returncode}): "
                                   f"{err.decode(errors='replace')[-800:].strip()}")
            ok = True
        finally:
            if not ok and p.poll() is None:          # e.g. the frame generator raised: stop FFmpeg
                p.kill()
            for f in (p.stdin, p.stderr):
                try:
                    f.close()
                except OSError:                      # BrokenPipeError flushing what FFmpeg never read
                    pass
            p.wait()
            if not ok:
                tmp.unlink(missing_ok=True)
        tmp.rename(out)
        return out

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
        """
        fps = int(cfg["output"]["fps"])
        inputs = []
        for s in segment_files:
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
        parts.append(f"{v}format=yuv420p,setsar=1[vout]")
        stems = {} if stems_dir else None
        mix, am = filters.final_mix(a, total, cfg, idx_music, idx_sfx, has_voice, stems)
        parts.append(mix)
        script = workdir / "_filter.txt"
        script.write_text(";\n".join(parts))
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

    def _mux_aac(self, src: Path, audio: Path, cfg: dict, out: Path) -> dict:
        run(["ffmpeg", "-i", src, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             "-c:a", "aac", "-b:a", f"{int(cfg['export']['audio_kbps'])}k", "-ar", "48000",
             "-movflags", "+faststart", out])
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
        drifted the aim is corrected, and the pass is repeated. Returns
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
        try:
            run(["ffmpeg", "-i", src, "-map", "0:a", "-c:a", "pcm_s24le", original])
            ceiling, aim = peak - 1.2, target        # margin for the true peak after AAC encoding
            gain = min(target - before, max_gain)
            for _ in range(4):
                gain, _measured = self._converge(original, gain, aim, ceiling, max_gain, work)
                final = self._mux_aac(src, work, cfg, out)
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
                final = self._mux_aac(src, work, cfg, out)
        finally:
            original.unlink(missing_ok=True)
            work.unlink(missing_ok=True)
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
