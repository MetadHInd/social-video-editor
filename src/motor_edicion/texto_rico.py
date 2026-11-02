"""Texto con emojis a color (Apple Color Emoji) dibujado con Pillow.

- Un emoji compuesto (con ZWJ o selector de variación) cuenta como UNA unidad, así el efecto de
  escritura nunca muestra medio emoji.
- El reparto en líneas se calcula con la frase completa: mientras se escribe, nada cambia de sitio.
"""
from __future__ import annotations

import re
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

FUENTE_EMOJI = "/System/Library/Fonts/Apple Color Emoji.ttc"
_E = "\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿"
_EMOJI = re.compile(f"[{_E}]️?(?:‍[{_E}]️?)*")


def unidades(texto: str) -> list[str]:
    """Divide en unidades visibles: letras sueltas o emojis completos."""
    out, i = [], 0
    for m in _EMOJI.finditer(texto):
        out += list(texto[i:m.start()])
        out.append(m.group())
        i = m.end()
    out += list(texto[i:])
    return [u for u in out if u not in ("️", "‍")]


def es_emoji(u: str) -> bool:
    return bool(_EMOJI.fullmatch(u))


@lru_cache(maxsize=512)
def _emoji_img(u: str, tam: int) -> Image.Image:
    f = ImageFont.truetype(FUENTE_EMOJI, 160)
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((0, 0), u, font=f, embedded_color=True)
    bb = im.getbbox() or (0, 0, 160, 160)
    im = im.crop((0, 0, max(bb[2], 160), max(bb[3], 160)))
    lado = int(tam * 1.0)
    return im.resize((lado, int(im.height * lado / im.width)), Image.LANCZOS)


def ancho_unidad(u: str, f, tam: int) -> float:
    return tam * 1.08 if es_emoji(u) else f.getlength(u)


def ancho(us: list[str], f, tam: int) -> float:
    return sum(ancho_unidad(u, f, tam) for u in us)


def partir(texto: str, f, tam: int, max_px: float) -> list[list[str]]:
    """Reparte en líneas (por palabras). Devuelve listas de unidades; los espacios se conservan."""
    palabras = texto.split(" ")
    lineas, actual = [], []
    for p in palabras:
        up = unidades(p)
        prueba = actual + ([" "] if actual else []) + up
        if ancho(prueba, f, tam) <= max_px or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = up
    if actual:
        lineas.append(actual)
    return lineas


def dibujar(img: Image.Image, x: float, y: float, us: list[str], f, tam: int, color, borde: int = 0,
            color_borde=(0, 0, 0, 255)) -> float:
    """Dibuja una línea de unidades desde (x, y = parte superior). Devuelve la x final."""
    d = ImageDraw.Draw(img)
    asc, _ = f.getmetrics()
    buf = ""
    x0 = x

    def soltar():
        nonlocal buf, x0
        if buf:
            kw = {"stroke_width": borde, "stroke_fill": color_borde} if borde else {}
            d.text((x0, y), buf, font=f, fill=color, **kw)
            x0 += f.getlength(buf)
            buf = ""

    for u in us:
        if es_emoji(u):
            soltar()
            e = _emoji_img(u, tam)
            img.alpha_composite(e, (int(x0 + tam * 0.04), int(y + asc - e.height * 0.86)))
            x0 += tam * 1.08
        else:
            buf += u
    soltar()
    return x0
