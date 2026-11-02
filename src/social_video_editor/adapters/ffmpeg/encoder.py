"""VideoEncoder implemented with FFmpeg."""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from ...domain.effects import freeze_seconds
from ...log import log
from ..system.hwaccel import decode_args, proxy_encoder_args, run_with_fallback
from ..system.tools import FFMPEG
from . import filters
from .runner import run, stderr_of

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

    def encode_frames(self, frames: Iterable[bytes], n: int, W: int, H: int, fps: int, out: Path) -> Path:
        """Encodes n raw RGB frames + silent audio, like the other shots."""
        tmp = out.with_suffix(".tmp.mkv")
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
               "-map", "0:v", "-map", "1:a", "-frames:v", str(n), "-t", f"{n / fps:.4f}",
               "-c:v", "libx264", "-preset", "fast", "-crf", "12", "-pix_fmt", "yuv420p",
               "-c:a", "pcm_s16le", "-ar", "48000", str(tmp)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for frame in frames:
            p.stdin.write(frame)
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError(p.stderr.read().decode()[-800:])
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
                has_voice: bool, draft: bool, workdir: Path, out: Path) -> Path:
        """Joins the segments, overlays the text layer and progress bar, mixes music + sfx → premaster."""
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
        mix, am = filters.final_mix(a, total, cfg, idx_music, idx_sfx, has_voice)
        parts.append(mix)
        script = workdir / "_filter.txt"
        script.write_text(";\n".join(parts))
        run(["ffmpeg", *inputs, "-/filter_complex", script, "-map", "[vout]", "-map", am,
             "-t", f"{total:.4f}", "-r", str(fps), *filters.video_output_options(cfg, draft),
             "-c:a", "pcm_s16le", "-ar", "48000", out])
        return out

    def measure_loudness(self, path: Path, cfg: dict) -> dict:
        a = cfg["audio"]
        err = stderr_of(["-i", str(path), "-vn", "-af",
                         f"loudnorm=I={a['loudness']}:TP={a['true_peak']}:LRA=11:print_format=json", "-f", "null", "-"])
        block = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
        return json.loads(block[-1]) if block else {}

    def master_audio(self, src: Path, out: Path, cfg: dict) -> dict:
        """Brings the audio to −14 LUFS with at most −1 dBTP. The video stream is copied.

        Gain + limiter in up to 5 passes: ASMR sounds (tearing, opening) have very high peaks and
        a linear normalizer cannot raise the level without exceeding the peak.
        """
        a = cfg["audio"]
        target, peak = float(a["loudness"]), float(a["true_peak"])
        m0 = self.measure_loudness(src, cfg)
        if not m0:
            log("Warning: could not measure loudness; copying without mastering.")
            run(["ffmpeg", "-i", src, "-c", "copy", out])
            return {}
        limit = 10 ** ((peak - 1.2) / 20)           # margin for the true peak after AAC encoding
        original = src.with_name(src.stem + "_audio.wav")
        work = src.with_name(src.stem + "_master.wav")
        run(["ffmpeg", "-i", src, "-map", "0:a", "-c:a", "pcm_s24le", original])
        gain, measured = 0.0, float(m0["input_i"])
        for _ in range(5):   # the limiter "eats" loudness: correct the total gain on the original
            if abs(measured - target) < 0.3:
                break
            gain += target - measured
            run(["ffmpeg", "-i", original, "-af",
                 f"volume={gain:.2f}dB,aresample=192000,"     # 4x oversampling: controls the true peak
                 f"alimiter=limit={limit:.4f}:attack=1:release=60:level=0,aresample=48000",
                 "-c:a", "pcm_s24le", work])
            measured = float(self.measure_loudness(work, cfg).get("input_i", target))
        run(["ffmpeg", "-i", src, "-i", work if work.exists() else original,
             "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             "-c:a", "aac", "-b:a", f"{int(cfg['export']['audio_kbps'])}k", "-ar", "48000",
             "-movflags", "+faststart", out])
        original.unlink(missing_ok=True)
        work.unlink(missing_ok=True)
        final = self.measure_loudness(out, cfg)
        return {"before_lufs": m0.get("input_i"), "after_lufs": final.get("input_i"),
                "true_peak_dbtp": final.get("input_tp")}

    def cover(self, video: Path, t: float, out: Path) -> Path:
        run(["ffmpeg", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "2", out])
        return out
