"""Toma "collage": cuadrícula con un cuadro de cada producto que aparecen uno a uno al ritmo.

En el plan: {"tipo": "collage", "de_tomas": [2, 3, 4, …], "dur": 4.2, "fondo_de": 15,
             "columnas": 4, "transicion": "zoom"}
- de_tomas: tomas de las que se saca un cuadro (el del medio de cada una).
- fondo_de: toma cuyo último cuadro, desenfocado, hace de fondo.
- Las fotos aparecen en los golpes de la música que caen dentro de la toma (o repartidas en el
  primer 60 % si no hay música), con un pequeño rebote y giro.
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

from . import animacion as an


def tiempos_aparicion(tm: dict, n: int, subdivision: int = 2) -> list[float]:
    """Cuándo aparece cada foto: en los golpes divididos en 'subdivision' partes
    (1 = una por golpe, 2 = por medio golpe, 4 = por cuarto de golpe: más rápido)."""
    dur = float(tm["dur"])
    sub = int(tm.get("subdivision", subdivision))
    golpes = [g for g in tm.get("_golpes", []) if g < dur]
    if len(golpes) >= 2:
        T = golpes[1] - golpes[0]
        inicio = golpes[0] - T * int(golpes[0] / T)        # incluye el golpe del arranque si cabe
        rejilla = [inicio + k * T / sub for k in range(int(dur / (T / sub)) + 1)]
        rejilla = [g for g in rejilla if g >= 0.05]
        if len(rejilla) >= n:
            return rejilla[:n]
    return [0.1 + k * (dur * 0.5) / max(n, 1) for k in range(n)]


def render(tm: dict, segs: list[Path], fondo_seg: Path, W: int, H: int, fps: int, cfg: dict, ruta: Path) -> Path:
    c = cfg.get("collage", {})
    cols = int(tm.get("columnas", c.get("columnas", 4)))
    filas = (len(segs) + cols - 1) // cols
    zona_y0, zona_y1 = float(c.get("y0", 0.215)), float(c.get("y1", 0.72))
    gap = int(W * 0.022)
    cw = int((W * 0.94 - gap * (cols - 1)) / cols)
    ch = int(cw * 4 / 3)
    alto_total = filas * ch + (filas - 1) * gap
    if alto_total > H * (zona_y1 - zona_y0):              # que quepa en la zona segura
        ch = int((H * (zona_y1 - zona_y0) - gap * (filas - 1)) / filas)
        cw = int(ch * 3 / 4)
        alto_total = filas * ch + (filas - 1) * gap
    x0 = (W - (cols * cw + (cols - 1) * gap)) / 2
    y0 = H * (zona_y0 + zona_y1) / 2 - alto_total / 2
    rnd = random.Random(3)
    celdas = []
    for k, s in enumerate(segs):
        im = an.cuadro_de(s, "t", t=_medio(s))
        im = an.recorte_centro(im, 3 / 4).resize((cw - 12, ch - 12), Image.LANCZOS)
        marco = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
        marco.paste(im, (6, 6))
        celda = an.sombra(an.esquinas(marco, int(cw * 0.07)), radio=10, opacidad=120, desplaz=8)
        cx = x0 + (k % cols) * (cw + gap) + cw / 2
        cy = y0 + (k // cols) * (ch + gap) + ch / 2
        celdas.append((celda, cx, cy, rnd.uniform(-5, 5)))
    fondo = an.fondo_suave(an.cuadro_de(fondo_seg, "ultimo"), W, H, oscuro=0.62)
    apar = tiempos_aparicion(tm, len(celdas), int(c.get("subdivision", 2)))
    tm["_apariciones"] = apar
    n = int(round(float(tm["dur"]) * fps))
    giradas = {}

    def cuadros():
        for i in range(n):
            t = i / fps
            lienzo = fondo.copy()
            for k, (celda, cx, cy, ang) in enumerate(celdas):
                dt = t - apar[k]
                if dt < 0:
                    continue
                esc = 0.55 + 0.6 * an.suavizado(dt / 0.07) - 0.15 * an.suavizado((dt - 0.07) / 0.09)
                esc = round(esc, 2)
                clave = (k, esc)
                if clave not in giradas:
                    im = celda.resize((max(int(celda.width * esc), 1), max(int(celda.height * esc), 1)),
                                      Image.BICUBIC)
                    giradas[clave] = im.rotate(ang, resample=Image.BICUBIC, expand=True)
                im = giradas[clave]
                lienzo.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))
            yield lienzo

    return an.escribir_segmento(cuadros(), n, W, H, fps, ruta)


def _medio(seg: Path) -> float:
    from .util import sondear
    return sondear(seg)["duracion"] * 0.55
