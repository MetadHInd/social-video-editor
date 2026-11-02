"""Efectos de toma (cámara) y efectos de sonido.

Efectos que puedes poner en "efectos" de una toma del plan:
  {"tipo": "zoom_golpe", "t": 0.0, "escala": 1.12}   acercamiento brusco (énfasis, "reveal")
  {"tipo": "empuje", "cantidad": 0.06}                acercamiento lento durante toda la toma
  {"tipo": "alejar", "cantidad": 0.06}                alejamiento lento
  {"tipo": "sacudida", "t": 0.0, "dur": 0.3}          temblor corto (impacto)
  {"tipo": "flash", "t": 0.0}                         destello blanco breve
  {"tipo": "congelar", "seg": 0.6}                    congela el último cuadro (también se puede poner "congelar" en la toma)
  {"tipo": "pulso_ritmo", "cantidad": 0.04}           pequeño zoom en cada golpe de la música dentro de la toma
Para crear uno nuevo: añade su lógica en expresion_zoom / expresion_crop / filtros_extra.
"""
from __future__ import annotations

from pathlib import Path

from .util import ejecutar, espacio


def _efectos(toma: dict, tipo: str) -> list[dict]:
    return [e for e in toma.get("efectos", []) if e.get("tipo") == tipo]


def expresion_zoom(toma: dict, cfg: dict, dur: float, indice: int) -> str:
    """Factor de zoom en función del tiempo t (segundos dentro de la toma ya acelerada)."""
    margen = float(cfg["encuadre"].get("margen_zoom", 1.25))
    base = float(toma.get("zoom", 1.0))
    partes = [f"{base:.4f}"]
    explicitos = _efectos(toma, "empuje") + _efectos(toma, "alejar")
    kb = float(cfg["encuadre"].get("empuje_lento", 0.0))
    if explicitos:
        for e in explicitos:
            c = float(e.get("cantidad", 0.06))
            partes.append(f"{c}*t/{dur:.4f}" if e["tipo"] == "empuje" else f"{c}*(1-t/{dur:.4f})")
    elif kb > 0 and dur > 0:  # empuje lento automático alternando dirección
        partes.append(f"{kb}*t/{dur:.4f}" if indice % 2 == 0 else f"{kb}*(1-t/{dur:.4f})")
    for e in _efectos(toma, "zoom_golpe"):
        esc = float(e.get("escala", cfg["efectos"].get("zoom_escala", 1.12))) - 1
        t0 = float(e.get("t", 0.0))
        if t0 <= 0.01:   # al inicio: arranca acercado y se asienta (golpe de entrada)
            partes.append(f"{esc:.3f}*max(0,1-t/0.35)*max(0,1-t/0.35)")
        else:
            partes.append(f"{esc:.3f}*min(max((t-{t0:.3f})/0.08,0),1)")
    for e in _efectos(toma, "pulso_ritmo"):    # golpes de la música (tiempos relativos a la toma)
        c = float(e.get("cantidad", cfg["efectos"].get("pulso_cantidad", 0.04)))
        for tb in toma.get("_golpes", [])[:12]:
            partes.append(f"{c:.3f}*gte(t,{tb:.3f})*exp(-(t-{tb:.3f})*9)")
    expr = "+".join(partes)
    if expr in ("1.0000",):
        return "1"
    return f"min({expr},{margen:.3f})"


def expresion_crop(toma: dict, fx: float, fy: float) -> tuple[str, str]:
    x, y = f"(iw-ow)*{fx:.3f}", f"(ih-oh)*{fy:.3f}"
    for e in _efectos(toma, "sacudida"):
        t0, d, a = float(e.get("t", 0)), float(e.get("dur", 0.3)), float(e.get("amplitud", 14))
        env = f"between(t,{t0:.3f},{t0 + d:.3f})*(1-(t-{t0:.3f})/{d:.3f})"
        x += f"+{a}*sin(t*73)*{env}"
        y += f"+{a * 0.7}*cos(t*61)*{env}"
    return f"'max(0,min(iw-ow,{x}))'", f"'max(0,min(ih-oh,{y}))'"


def filtros_extra(toma: dict) -> str:
    f = []
    for e in _efectos(toma, "flash"):
        t0 = float(e.get("t", 0))
        f.append(f"eq=brightness='0.45*max(0,1-abs(t-{t0:.3f})/0.09)':eval=frame")
    return ",".join(f)


def congelar(toma: dict) -> float:
    s = float(toma.get("congelar", 0) or 0)
    for e in _efectos(toma, "congelar"):
        s += float(e.get("seg", 0.6))
    return s


# ── Efectos de sonido (se generan si no existen; reemplázalos con tus propios .wav del mismo nombre) ──
_RECETAS_SFX = {
    "whoosh": ["-f", "lavfi", "-i", "anoisesrc=color=pink:d=0.45:a=0.6:r=48000",
               "-af", "bandpass=f=1400:w=1800,afade=t=in:d=0.18:curve=exp,afade=t=out:st=0.18:d=0.27,volume=1.6"],
    "pop": ["-f", "lavfi", "-i",
            "aevalsrc='0.7*sin(2*PI*(500+1600*exp(-t*45))*t)*exp(-t*28)':d=0.14:s=48000"],
    "flash": ["-f", "lavfi", "-i",
              "aevalsrc='0.35*sin(2*PI*1760*t)*exp(-t*7)+0.2*sin(2*PI*2637*t)*exp(-t*9)"
              "+0.12*sin(2*PI*3520*t)*exp(-t*12)':d=0.7:s=48000"],
    "click": ["-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*2200*t)*exp(-t*90)':d=0.05:s=48000"],
}


def _sintetizar(nombre: str, ruta: Path) -> bool:
    """Sonidos que se crean con numpy (más naturales que con FFmpeg)."""
    import numpy as np
    import soundfile as sf

    sr = 48000
    rng = np.random.default_rng(7)
    if nombre == "tecla":      # tecla suave de teclado
        n = int(sr * 0.045)
        t = np.arange(n) / sr
        ruido = rng.standard_normal(n) * np.exp(-t * 160)
        tono = 0.5 * np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 260)
        x = np.convolve(ruido, np.ones(6) / 6, mode="same") + tono
    elif nombre == "obturador":   # cámara de fotos: dos chasquidos mecánicos + aire
        n = int(sr * 0.32)
        t = np.arange(n) / sr
        x = np.zeros(n)
        for t0, a in ((0.0, 1.0), (0.11, 0.7)):
            m = t >= t0
            tt = t[m] - t0
            x[m] += a * (rng.standard_normal(m.sum()) * np.exp(-tt * 90)
                         + 0.6 * np.sin(2 * np.pi * 1800 * tt) * np.exp(-tt * 140))
        x += 0.15 * rng.standard_normal(n) * np.exp(-t * 18)
    else:
        return False
    x = 0.8 * x / (np.abs(x).max() + 1e-9)
    sf.write(ruta, np.stack([x, x], 1).astype("float32"), sr)
    return True


def sfx(nombre: str) -> Path:
    carpeta = espacio() / "sfx"                      # <espacio>/sfx: se pueden reemplazar por sonidos propios
    carpeta.mkdir(exist_ok=True)
    ruta = carpeta / f"{nombre}.wav"
    if not ruta.exists() and not _sintetizar(nombre, ruta):
        if nombre not in _RECETAS_SFX:
            raise SystemExit(f"No existe sfx/{nombre}.wav")
        ejecutar(["ffmpeg", *_RECETAS_SFX[nombre], "-ac", "2", "-ar", "48000", ruta])
    return ruta
