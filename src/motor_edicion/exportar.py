"""Perfiles de exportación por plataforma, portada y archivo .srt."""
from __future__ import annotations

from pathlib import Path

from .util import ejecutar

PERFILES = {
    # nombre:   ancho, alto, crf, tasa máx.  (H.264 High, yuv420p, BT.709, AAC 48 kHz)
    "tiktok":  {"ancho": 1080, "alto": 1920, "crf": 17, "maxrate": "20M"},
    "reels":   {"ancho": 1080, "alto": 1920, "crf": 17, "maxrate": "15M"},
    "shorts":  {"ancho": 1080, "alto": 1920, "crf": 17, "maxrate": "20M"},
    "youtube": {"ancho": 1920, "alto": 1080, "crf": 18, "maxrate": "16M"},
    "cuadrado": {"ancho": 1080, "alto": 1080, "crf": 17, "maxrate": "15M"},
}


def opciones_video(cfg: dict, borrador: bool) -> list[str]:
    e = cfg["exportar"]
    if borrador:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p"]
    mr = e.get("maxrate", "20M")
    buf = f"{int(str(mr).rstrip('M')) * 2}M" if str(mr).endswith("M") else mr
    return ["-c:v", "libx264", "-preset", "slow", "-crf", str(e.get("crf", 17)), "-maxrate", str(mr),
            "-bufsize", buf, "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-g", str(int(cfg["salida"]["fps"]) * 2)]


def _ts(s: float) -> str:
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def srt(grupos: list[dict], textos: list[dict], ruta: Path) -> Path:
    """Subtítulos en .srt (para TikTok, YouTube o DaVinci)."""
    items = [(g["ini"], g["fin"], " ".join(w["p"] for w in g["palabras"])) for g in grupos]
    if not items:
        items = [(t["ini"], t["fin"], t["texto"]) for t in textos]
    items.sort()
    bloques = [f"{i}\n{_ts(a)} --> {_ts(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(items, 1)]
    ruta.write_text("\n".join(bloques), encoding="utf-8")
    return ruta


def portada(video: Path, t: float, ruta: Path) -> Path:
    ejecutar(["ffmpeg", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "2", ruta])
    return ruta
