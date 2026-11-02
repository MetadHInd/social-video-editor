"""Utilidades comunes: ejecutar FFmpeg, leer metadatos, rutas y registro."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PAQUETE = Path(__file__).resolve().parent               # código del motor (solo lectura)
RECETAS_PAQUETE = PAQUETE / "recetas"                    # recetas que vienen con el paquete
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def espacio() -> Path:
    """Carpeta de trabajo (proyectos/, musica/, sfx/, recetas/ propias): $MOTOR_ESPACIO o la carpeta actual."""
    return Path(os.environ.get("MOTOR_ESPACIO") or Path.cwd()).expanduser().resolve()


def dir_cache() -> Path:
    """Caché del usuario para archivos generados (LUTs…): $XDG_CACHE_HOME/motor_edicion o ~/.cache/motor_edicion."""
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base).expanduser() / "motor_edicion"


def log(msg: str) -> None:
    print(f"[motor {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ejecutar(cmd: list[str], silencioso: bool = True) -> subprocess.CompletedProcess:
    """Ejecuta un comando; si falla muestra el final del error de FFmpeg."""
    cmd = [str(c) for c in cmd]
    if cmd[0] == "ffmpeg":
        cmd[0] = FFMPEG
        cmd[1:1] = ["-hide_banner", "-y", "-nostdin"]
        if silencioso:
            cmd[1:1] = ["-loglevel", "error"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("\n".join(r.stderr.splitlines()[-25:]) + "\n")
        raise RuntimeError(f"Falló: {' '.join(cmd[:6])} …")
    return r


def ffmpeg_stderr(cmd: list[str]) -> str:
    """Ejecuta FFmpeg devolviendo el stderr (para filtros de medición como ebur128/loudnorm)."""
    cmd = [FFMPEG, "-hide_banner", "-nostdin", *[str(c) for c in cmd]]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("\n".join(r.stderr.splitlines()[-20:]))
    return r.stderr


def sondear(ruta: str | Path) -> dict:
    """Metadatos de un archivo de video: tamaño mostrado (con rotación), fps, HDR, audio."""
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)],
        capture_output=True, text=True, check=True,
    )
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    audios = [s for s in d["streams"] if s["codec_type"] == "audio" and s.get("codec_name") != "apple_apac"]
    info = {
        "ruta": str(ruta),
        "duracion": float(d["format"]["duration"]),
        "audio": bool(audios),
        "audio_canales": int(audios[0]["channels"]) if audios else 0,
        "audio_indice": int(audios[0]["index"]) if audios else None,
    }
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []):
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) in (90, 270):
            w, h = h, w
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
        transfer = v.get("color_transfer", "")
        info.update(
            ancho=w, alto=h, rotacion=rot, fps=float(num) / float(den or 1),
            codec=v.get("codec_name"), pix_fmt=v.get("pix_fmt"),
            transfer=transfer, primaries=v.get("color_primaries", ""),
            hdr=transfer in ("arib-std-b67", "smpte2084"),
            hlg=transfer == "arib-std-b67",
        )
    return info


def mmss(seg: float) -> str:
    m, s = divmod(max(seg, 0), 60)
    return f"{int(m):02d}:{s:05.2f}"


def guardar_json(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def leer_json(ruta: Path):
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def fuente_id(ruta: str | Path) -> str:
    """Nombre corto y seguro para archivos de caché."""
    s = Path(ruta).stem
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")
