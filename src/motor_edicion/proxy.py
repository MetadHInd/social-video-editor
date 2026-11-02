"""Copias livianas (proxies) para analizar rápido sin tocar los originales.

Por cada fuente crea en cache/:
  <id>_proxy.mp4   video 540×960 (o proporcional) ya en SDR, 30 fps
  <id>_16k.wav     audio mono 16 kHz (transcripción)
  <id>_info.json   metadatos (tamaño, rotación, HDR, audio…)
"""
from __future__ import annotations

from pathlib import Path

from .color import cadena_sdr
from .util import ejecutar, fuente_id, guardar_json, leer_json, log, sondear

LADO_CORTO = 540


def preparar(fuente: dict, cache: Path, cfg: dict) -> dict:
    ruta = fuente["ruta"]
    fid = fuente_id(ruta)
    info_p = cache / f"{fid}_info.json"
    info = leer_json(info_p) if info_p.exists() else sondear(ruta)
    info["id"] = fid
    guardar_json(info_p, info)

    proxy = cache / f"{fid}_proxy.mp4"
    if not proxy.exists():
        log(f"Proxy de {Path(ruta).name} ({info['duracion']:.0f} s)…")
        if info["ancho"] <= info["alto"]:
            w, h = LADO_CORTO, round(LADO_CORTO * info["alto"] / info["ancho"] / 2) * 2
        else:
            h, w = LADO_CORTO, round(LADO_CORTO * info["ancho"] / info["alto"] / 2) * 2
        vf = f"scale={w}:{h}:flags=bicubic,{cadena_sdr(info, cfg['color'].get('hdr_blanco', 0.62))},fps=30"
        cmd = ["ffmpeg", "-hwaccel", "videotoolbox", "-i", ruta, "-map", "0:v:0", "-vf", vf,
               "-c:v", "h264_videotoolbox", "-b:v", "3M", "-an", proxy.with_suffix(".tmp.mp4")]
        ejecutar(cmd)
        proxy.with_suffix(".tmp.mp4").rename(proxy)
    info["proxy"] = str(proxy)

    wav = cache / f"{fid}_16k.wav"
    if info["audio"] and not wav.exists():
        ejecutar(["ffmpeg", "-i", ruta, "-map", f"0:{info['audio_indice']}", "-ac", "1", "-ar", "16000",
                  "-c:a", "pcm_s16le", wav])
    info["wav"] = str(wav) if info["audio"] else None
    guardar_json(info_p, info)
    return info
