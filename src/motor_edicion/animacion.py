"""Utilidades para tomas "dibujadas" (polaroid, collage…): de cuadros de Pillow a un segmento de video."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .util import FFMPEG, ejecutar


def cuadro_de(video: Path, cuando: str = "ultimo", t: float | None = None) -> Image.Image:
    """Un fotograma de un segmento ya renderizado (ya tiene color corregido y encuadre)."""
    tmp = video.with_suffix(f".{cuando}.png")
    if cuando == "ultimo":
        ejecutar(["ffmpeg", "-sseof", "-0.12", "-i", video, "-update", "1", "-frames:v", "1", tmp])
    else:
        ejecutar(["ffmpeg", "-ss", f"{t or 0:.3f}", "-i", video, "-frames:v", "1", tmp])
    im = Image.open(tmp).convert("RGB")
    tmp.unlink(missing_ok=True)
    return im


def fondo_suave(im: Image.Image, W: int, H: int, oscuro: float = 0.55, tinte=(255, 214, 230)) -> Image.Image:
    """Fondo: la misma imagen muy desenfocada, más oscura y con un tinte pastel."""
    f = im.resize((W // 4, H // 4)).filter(ImageFilter.GaussianBlur(10)).resize((W, H), Image.BILINEAR)
    capa = Image.new("RGB", (W, H), tinte)
    f = Image.blend(f, capa, 0.18)
    return Image.eval(f, lambda v: int(v * oscuro)).convert("RGBA")


def esquinas(im: Image.Image, radio: int) -> Image.Image:
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=radio, fill=255)
    out = im.convert("RGBA")
    out.putalpha(m)
    return out


def sombra(im: Image.Image, radio: int = 18, opacidad: int = 120, desplaz: int = 14) -> Image.Image:
    """Devuelve la imagen con su sombra suave (lienzo un poco más grande)."""
    m = radio * 2 + desplaz
    lienzo = Image.new("RGBA", (im.width + 2 * m, im.height + 2 * m), (0, 0, 0, 0))
    alfa = im.split()[-1].point(lambda a: a * opacidad // 255)
    negro = Image.new("RGBA", im.size, (0, 0, 0, 255))
    negro.putalpha(alfa)
    lienzo.alpha_composite(negro, (m, m + desplaz))
    lienzo = lienzo.filter(ImageFilter.GaussianBlur(radio))
    lienzo.alpha_composite(im, (m, m))
    return lienzo


def recorte_centro(im: Image.Image, aspecto: float, cy: float = 0.5) -> Image.Image:
    """Recorta al aspecto (ancho/alto) dado, centrado (cy = centro vertical 0-1)."""
    w, h = im.size
    if w / h > aspecto:
        nw = int(h * aspecto)
        x = (w - nw) // 2
        return im.crop((x, 0, x + nw, h))
    nh = int(w / aspecto)
    y = int(min(max(h * cy - nh / 2, 0), h - nh))
    return im.crop((0, y, w, y + nh))


def escribir_segmento(cuadros, n: int, W: int, H: int, fps: int, ruta: Path) -> Path:
    """Codifica n cuadros (iterador de imágenes RGB/RGBA) + audio en silencio, como las demás tomas."""
    tmp = ruta.with_suffix(".tmp.mkv")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-map", "0:v", "-map", "1:a", "-frames:v", str(n), "-t", f"{n / fps:.4f}",
           "-c:v", "libx264", "-preset", "fast", "-crf", "12", "-pix_fmt", "yuv420p",
           "-c:a", "pcm_s16le", "-ar", "48000", str(tmp)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for im in cuadros:
        p.stdin.write(im.convert("RGB").tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(p.stderr.read().decode()[-800:])
    tmp.rename(ruta)
    return ruta


def suavizado(x: float) -> float:
    """Curva de salida suave 0→1."""
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3
