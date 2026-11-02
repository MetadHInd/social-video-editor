"""Catálogo de transiciones (cómo ENTRA una toma).

Cada nombre en español se traduce a una transición de FFmpeg (xfade) y, si corresponde,
a un efecto de sonido. Para agregar una nueva: añade una línea a CATALOGO.
Lista completa de xfade: https://trac.ffmpeg.org/wiki/Xfade
"""
from __future__ import annotations

# nombre: (transición xfade | None = corte seco, duración por defecto, sonido)
CATALOGO = {
    "corte":        (None, 0.0, None),
    "fundido":      ("fade", 0.35, None),
    "negro":        ("fadeblack", 0.4, None),
    "flash":        ("fadewhite", 0.18, "flash"),
    "zoom":         ("zoomin", 0.25, "whoosh"),
    "whip":         ("smoothleft", 0.22, "whoosh"),
    "slide_arriba": ("slideup", 0.22, "whoosh"),
    "desliz_izq":   ("slideleft", 0.22, "whoosh"),
    "circulo":      ("circleopen", 0.35, "whoosh"),
    "pixel":        ("pixelize", 0.3, None),
    "difuminar":    ("hblur", 0.3, None),
    "radial":       ("radial", 0.3, "whoosh"),
    "cortina":      ("vertopen", 0.3, "whoosh"),
}


def info(nombre: str) -> tuple:
    if nombre not in CATALOGO:
        raise SystemExit(f"Transición desconocida '{nombre}'. Opciones: {', '.join(CATALOGO)}")
    return CATALOGO[nombre]


def duracion_transicion(nombre: str, cfg: dict) -> float:
    xf, dur, _ = info(nombre)
    if xf is None:
        return 0.0
    base = float(cfg.get("transiciones", {}).get("duracion", dur))
    return dur if nombre in ("fundido", "negro", "circulo", "pixel", "difuminar", "radial", "cortina") else base


def unir(segmentos: list[dict], fps: int) -> tuple[str, str, str]:
    """Filtro que une los segmentos [i:v]/[i:a] con sus transiciones.

    segmentos: [{"dur": s, "solape": s, "transicion": nombre}, …] (en orden, entradas 0..n-1)
    Devuelve (filtro, etiqueta_video, etiqueta_audio).
    """
    # entradas normalizadas (misma base de tiempo: xfade lo exige)
    partes = [f"[{i}:v]fps={fps},settb=AVTB,setpts=PTS-STARTPTS[sv{i}];[{i}:a]asetpts=PTS-STARTPTS[sa{i}]"
              for i in range(len(segmentos))]
    v, a = "[sv0]", "[sa0]"
    largo = segmentos[0]["dur"]
    for i, s in enumerate(segmentos[1:], start=1):
        xf, _, _ = info(s["transicion"])
        nv, na = f"[v{i}]", f"[a{i}]"
        if xf is None or s["solape"] <= 0:
            partes.append(f"{v}{a}[sv{i}][sa{i}]concat=n=2:v=1:a=1[cv{i}]{na};[cv{i}]settb=AVTB{nv}")
            largo += s["dur"]
        else:
            off = largo - s["solape"]
            partes.append(f"{v}[sv{i}]xfade=transition={xf}:duration={s['solape']:.4f}:offset={off:.4f}{nv}")
            partes.append(f"{a}[sa{i}]acrossfade=d={s['solape']:.4f}:c1=tri:c2=tri{na}")
            largo += s["dur"] - s["solape"]
        v, a = nv, na
    return ";".join(partes), v, a
