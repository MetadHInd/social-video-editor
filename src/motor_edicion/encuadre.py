"""Encuadre: llevar cualquier fuente (vertical, horizontal, cuadrada) al formato de salida
y aplicar los movimientos de cámara de la toma (empuje lento, zoom de golpe, sacudida).

Truco de calidad: la fuente se escala primero a un tamaño mayor que la salida (margen_zoom)
para que los zooms recorten píxeles reales en vez de agrandar la imagen.
"""
from __future__ import annotations

from .efectos import expresion_crop, expresion_zoom


def _par(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def tamano_trabajo(W: int, H: int, cfg: dict) -> tuple[int, int]:
    m = float(cfg["encuadre"].get("margen_zoom", 1.25))
    return _par(W * m), _par(H * m)


def modo(info: dict, W: int, H: int, cfg: dict) -> str:
    m = cfg["encuadre"].get("modo", "auto")
    if m != "auto":
        return m
    r_src, r_out = info["ancho"] / info["alto"], W / H
    return "desenfoque" if max(r_src, r_out) / min(r_src, r_out) > 1.3 else "recortar"


def escala_previa(info: dict, W: int, H: int, cfg: dict, toma: dict) -> str:
    """Primer filtro (sobre la fuente original): la lleva al tamaño de trabajo."""
    Wm, Hm = tamano_trabajo(W, H, cfg)
    if modo(info, W, H, cfg) == "desenfoque":
        fit = min(Wm / info["ancho"], Hm / info["alto"])
        fw, fh = _par(info["ancho"] * fit), _par(info["alto"] * fit)
        cov = max(Wm / info["ancho"], Hm / info["alto"])
        cw, ch = _par(info["ancho"] * cov), _par(info["alto"] * cov)
        return (f"split=2[fondo][frente];"
                f"[fondo]scale={_par(cw / 4)}:{_par(ch / 4)},boxblur=20:2,scale={cw}:{ch},crop={Wm}:{Hm},"
                f"eq=brightness=-0.08[f2];"
                f"[frente]scale={fw}:{fh}:flags=lanczos[fr2];[f2][fr2]overlay=(W-w)/2:(H-h)/2")
    cov = max(Wm / info["ancho"], Hm / info["alto"])
    cw, ch = _par(info["ancho"] * cov), _par(info["alto"] * cov)
    fx = float(toma.get("enfoque_x", cfg["encuadre"].get("enfoque_x", 0.5)))
    fy = float(toma.get("enfoque_y", 0.5))
    crop = f",crop={Wm}:{Hm}:{int((cw - Wm) * fx)}:{int((ch - Hm) * fy)}" if (cw, ch) != (Wm, Hm) else ""
    return f"scale={cw}:{ch}:flags=lanczos{crop}"


def movimiento(W: int, H: int, cfg: dict, toma: dict, dur: float, indice: int) -> str:
    """Zoom variable en el tiempo + recorte final al tamaño de salida."""
    z = expresion_zoom(toma, cfg, dur, indice)
    fx = float(toma.get("enfoque_x", 0.5))
    fy = float(toma.get("enfoque_y", 0.5))
    x, y = expresion_crop(toma, fx, fy)
    if z == "1":
        escala = f"scale={W}:{H}:flags=bicubic"
    else:
        escala = (f"scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame:flags=bicubic")
    return f"{escala},crop={W}:{H}:{x}:{y}"
