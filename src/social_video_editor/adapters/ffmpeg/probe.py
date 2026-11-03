"""Media metadata with ffprobe: displayed size (with rotation), fps, HDR, audio.

Still photos (jpg, png, heic when pillow-heif is installed…) are read with Pillow instead: displayed
size (EXIF orientation applied), orientation, capture time (EXIF DateTimeOriginal, else DateTime,
else the file's modification time). They have no duration and no audio.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ...domain.photos import capture_time, displayed_size, is_image_name
from ..system.tools import FFPROBE

TAG_ORIENTATION, TAG_DATETIME, TAG_DATETIME_ORIGINAL, IFD_EXIF = 274, 306, 36867, 0x8769
HEIF_EXTENSIONS = (".heic", ".heif")


def _heif_support() -> bool:
    """Registers the optional HEIC/HEIF opener (pillow-heif) once; False when it is not installed."""
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return False
    register_heif_opener()
    return True


HEIF_SUPPORTED = _heif_support()


def is_image(path: str | Path) -> bool:
    """A still photo this engine can read (HEIC/HEIF only with pillow-heif installed)."""
    if not is_image_name(path):
        return False
    return HEIF_SUPPORTED or Path(path).suffix.lower() not in HEIF_EXTENSIONS


def scan_images(folder: str | Path) -> list[str]:
    """Photos inside `folder` and its subfolders (hidden files and folders skipped), sorted by path."""
    root = Path(folder).expanduser()
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel) or not p.is_file():
            continue
        if is_image(p):
            out.append(str(p))
    return sorted(out)


def image_info(path: str | Path) -> dict:
    """Media info of a still photo, with the same keys as a video's (duration 0, no audio)."""
    from PIL import Image

    with Image.open(path) as im:
        raw_w, raw_h = im.size
        exif = im.getexif()
        orientation = int(exif.get(TAG_ORIENTATION, 1) or 1)
        try:
            original = exif.get_ifd(IFD_EXIF).get(TAG_DATETIME_ORIGINAL)
        except (KeyError, ValueError, TypeError):
            original = None
        fmt = (im.format or Path(path).suffix.lstrip(".")).lower()
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        mtime = None
    taken, taken_from = capture_time(original, exif.get(TAG_DATETIME), mtime)
    w, h = displayed_size(raw_w, raw_h, orientation)
    return {"path": str(path), "image": True, "duration": 0.0, "has_audio": False, "audio_channels": 0,
            "audio_index": None, "width": w, "height": h, "rotation": 0, "orientation": orientation,
            "fps": None, "codec": fmt, "pix_fmt": None, "transfer": "", "primaries": "", "hdr": False,
            "hlg": False, "taken": taken, "taken_from": taken_from}


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
        if is_image(path):
            return image_info(path)
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
        if is_image(path):
            return 0.0
        return float(self._run(path)["format"]["duration"])

    def fingerprint(self, path: str | Path) -> dict:
        p = Path(path).expanduser().resolve()
        try:
            st = p.stat()
            size, mtime = st.st_size, st.st_mtime_ns
        except OSError:
            size = mtime = None
        return {"path": str(p), "size": size, "mtime_ns": mtime}
