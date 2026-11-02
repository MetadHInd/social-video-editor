"""Toma "polaroid": congela el producto dentro de una foto instantánea con flash.

En el plan: {"tipo": "polaroid", "de_toma": 5, "dur": 1.2, "texto": "Funda de unicornio 💛",
             "giro": -4, "transicion": "corte"}
- de_toma: índice de la toma cuyo ÚLTIMO cuadro se convierte en foto (continuidad: parece que se congela).
- La foto cae girando con un destello blanco, luego se acerca lentamente. Sonido de obturador automático.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from . import animacion as an
from . import texto_rico as tr
from .subtitulos import fuente


def _foto(frame: Image.Image, W: int, texto: str, cfg: dict) -> Image.Image:
    p = cfg.get("polaroid", {})
    ancho_foto = int(W * float(p.get("ancho", 0.70)))
    foto = an.recorte_centro(frame, 4 / 5, float(p.get("centro_y", 0.5))).resize(
        (ancho_foto, int(ancho_foto * 5 / 4)), Image.LANCZOS)
    m, abajo = int(ancho_foto * 0.06), int(ancho_foto * 0.27)
    marco = Image.new("RGBA", (ancho_foto + 2 * m, foto.height + m + abajo), (252, 250, 246, 255))
    marco.paste(foto, (m, m))
    if texto:
        tam = int(ancho_foto * 0.085)
        f = fuente(p.get("fuente", "Bradley Hand Bold"), tam)
        lineas = tr.partir(texto, f, tam, ancho_foto)
        y = foto.height + m + (abajo - tam * 1.2 * len(lineas)) / 2
        for ln in lineas:
            x = (marco.width - tr.ancho(ln, f, tam)) / 2
            tr.dibujar(marco, x, y, ln, f, tam, (60, 52, 58, 255))
            y += tam * 1.2
    ImageDraw.Draw(marco).rectangle([0, 0, marco.width - 1, marco.height - 1], outline=(225, 220, 215, 255))
    return an.sombra(marco, radio=22, opacidad=150, desplaz=18)


def render(tm: dict, seg_origen: Path, W: int, H: int, fps: int, cfg: dict, ruta: Path) -> Path:
    frame = an.cuadro_de(seg_origen, "ultimo")
    fondo = an.fondo_suave(frame, W, H)
    foto = _foto(frame, W, tm.get("texto", ""), cfg)
    giro = float(tm.get("giro", -4))
    n = int(round(float(tm["dur"]) * fps))
    blanco = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    caida = float(cfg.get("polaroid", {}).get("caida", 0.16))      # segundos que tarda en caer
    destello = float(cfg.get("polaroid", {}).get("destello", 0.10))

    def cuadros():
        for i in range(n):
            t = i / fps
            e = an.suavizado(t / caida)
            esc = 1.35 - 0.35 * e + 0.035 * max(t - caida, 0) / max(n / fps, 0.1)
            ang = giro - 10 * (1 - e)
            im = foto.resize((int(foto.width * esc), int(foto.height * esc)), Image.BICUBIC)
            im = im.rotate(ang, resample=Image.BICUBIC, expand=True)
            c = fondo.copy()
            c.alpha_composite(im, (int((W - im.width) / 2), int(H * 0.47 - im.height / 2)))
            flash = max(0.0, 1 - t / destello)               # destello al disparar
            if flash > 0:
                c = Image.blend(c, blanco, 0.85 * flash)
            yield c

    return an.escribir_segmento(cuadros(), n, W, H, fps, ruta)
