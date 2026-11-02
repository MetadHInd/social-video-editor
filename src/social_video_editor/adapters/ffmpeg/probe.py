"""Media metadata with ffprobe: displayed size (with rotation), fps, HDR, audio."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..system.tools import FFPROBE


def _rate(text) -> float | None:
    """"30000/1001" / "30" → frames per second; None for "0/0", "N/A", empty or invalid values."""
    try:
        num, _, den = str(text or "").partition("/")
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return fps if fps > 0 and fps == fps and fps != float("inf") else None


def frame_rate(stream: dict) -> float:
    """avg_frame_rate, else r_frame_rate (ffprobe reports "0/0" for some streams), else 30."""
    return _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate")) or 30.0


class FfprobeMediaProbe:
    def _run(self, path) -> dict:
        r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                            str(path)], capture_output=True, text=True, check=True)
        return json.loads(r.stdout)

    def probe(self, path: str | Path) -> dict:
        d = self._run(path)
        v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
        audios = [s for s in d["streams"] if s["codec_type"] == "audio" and s.get("codec_name") != "apple_apac"]
        info = {
            "path": str(path),
            "duration": float(d["format"]["duration"]),
            "has_audio": bool(audios),
            "audio_channels": int(audios[0]["channels"]) if audios else 0,
            "audio_index": int(audios[0]["index"]) if audios else None,
        }
        if v:
            w, h = int(v["width"]), int(v["height"])
            rot = 0
            for sd in v.get("side_data_list", []):
                if "rotation" in sd:
                    rot = int(sd["rotation"])
            if abs(rot) in (90, 270):
                w, h = h, w
            transfer = v.get("color_transfer", "")
            info.update(
                width=w, height=h, rotation=rot, fps=frame_rate(v),
                codec=v.get("codec_name"), pix_fmt=v.get("pix_fmt"),
                transfer=transfer, primaries=v.get("color_primaries", ""),
                hdr=transfer in ("arib-std-b67", "smpte2084"),
                hlg=transfer == "arib-std-b67",
            )
        return info

    def duration(self, path: str | Path) -> float:
        return float(self._run(path)["format"]["duration"])

    def fingerprint(self, path: str | Path) -> dict:
        p = Path(path).expanduser().resolve()
        try:
            st = p.stat()
            size, mtime = st.st_size, st.st_mtime_ns
        except OSError:
            size = mtime = None
        return {"path": str(p), "size": size, "mtime_ns": mtime}
