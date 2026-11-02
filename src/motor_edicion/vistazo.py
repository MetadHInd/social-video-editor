"""Hojas de miniaturas + transcripción: para MIRAR el material antes de decidir.

Crea en cache/vistazo/:
  <id>_hoja_01.jpg …  cuadrícula de fotogramas con su tiempo, barra de movimiento (verde) y de volumen (naranja)
  <id>_texto.txt      lo que se dice, con tiempos
Uso fino: motor vistazo proyectos/X --fuente 0 --desde 120 --hasta 150 --cada 1
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .util import mmss

FUENTE_ETIQUETA = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _fuente(tam: int):
    try:
        return ImageFont.truetype(FUENTE_ETIQUETA, tam)
    except OSError:
        return ImageFont.load_default()


def _valor_en(muestras: list[dict], clave: str, t: float) -> float:
    if not muestras:
        return 0.0
    ts = np.array([m["t"] for m in muestras])
    return float(muestras[int(np.clip(np.searchsorted(ts, t), 0, len(ts) - 1))][clave])


def hojas(info: dict, analisis: dict, salida: Path, desde: float = 0, hasta: float | None = None,
          cada: float | None = None, columnas: int = 6, filas: int = 5, ancho_mini: int = 170) -> list[Path]:
    import cv2

    salida.mkdir(parents=True, exist_ok=True)
    hasta = min(hasta or info["duracion"], info["duracion"])
    if cada is None:  # que todo el video quepa en ~4 hojas
        cada = max(1.0, round((hasta - desde) / (columnas * filas * 4)))
    tiempos = list(np.arange(desde, hasta, cada))
    cap = cv2.VideoCapture(info["proxy"])
    mov = [m["movimiento"] for m in analisis["muestras"]] or [1]
    mov_max = np.percentile(mov, 95) or 1
    audio = analisis.get("audio")
    alto_mini = int(ancho_mini * info["alto"] / info["ancho"])
    f_et = _fuente(15)
    rutas, por_hoja = [], columnas * filas
    sufijo = f"_{int(desde)}-{int(hasta)}s" if (desde or hasta < info["duracion"]) else ""
    for h in range(0, len(tiempos), por_hoja):
        lote = tiempos[h:h + por_hoja]
        hoja = Image.new("RGB", (columnas * (ancho_mini + 6) + 6, filas * (alto_mini + 30) + 6), (20, 20, 24))
        d = ImageDraw.Draw(hoja)
        for k, t in enumerate(lote):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, f = cap.read()
            if not ok:
                continue
            img = Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)).resize((ancho_mini, alto_mini))
            x = 6 + (k % columnas) * (ancho_mini + 6)
            y = 6 + (k // columnas) * (alto_mini + 30)
            hoja.paste(img, (x, y))
            d.text((x + 2, y + alto_mini + 2), mmss(t), font=f_et, fill=(255, 255, 255))
            m = min(_valor_en(analisis["muestras"], "movimiento", t) / mov_max, 1)
            d.rectangle([x, y + alto_mini + 21, x + int(ancho_mini * m), y + alto_mini + 24], fill=(80, 220, 120))
            if audio:
                i = min(int(t / audio["paso"]), len(audio["db"]) - 1)
                v = np.clip((audio["db"][i] + 60) / 60, 0, 1)
                d.rectangle([x, y + alto_mini + 25, x + int(ancho_mini * v), y + alto_mini + 28], fill=(250, 150, 50))
        ruta = salida / f"{info['id']}{sufijo}_hoja_{h // por_hoja + 1:02d}.jpg"
        hoja.save(ruta, quality=88)
        rutas.append(ruta)
    cap.release()
    return rutas


def texto(info: dict, analisis: dict, salida: Path) -> Path:
    """Transcripción agrupada en frases con su tiempo."""
    salida.mkdir(parents=True, exist_ok=True)
    lineas, frase, ini = [], [], None
    pal = analisis.get("palabras", [])
    for i, w in enumerate(pal):
        if ini is None:
            ini = w["ini"]
        frase.append(w["p"])
        corte = i == len(pal) - 1 or pal[i + 1]["ini"] - w["fin"] > 0.6 or w["p"][-1:] in ".?!"
        if corte:
            lineas.append(f"[{mmss(ini)} → {mmss(w['fin'])}] {' '.join(frase)}")
            frase, ini = [], None
    au = analisis.get("audio") or {}
    cab = [f"# {Path(info['ruta']).name} — {info['duracion']:.1f} s",
           f"# voz en {analisis['voz']['proporcion'] * 100:.0f}% del video; "
           f"{len(analisis['escenas'])} escenas; picos de sonido en: "
           + ", ".join(mmss(p) for p in au.get("picos", [])[:40]), ""]
    ruta = salida / f"{info['id']}_texto.txt"
    ruta.write_text("\n".join(cab + (lineas or ["(sin voz detectada)"])) + "\n", encoding="utf-8")
    return ruta
