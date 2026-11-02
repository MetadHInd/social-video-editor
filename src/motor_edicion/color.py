"""Color y luz.

1. Conversión HDR (HLG de iPhone) → SDR BT.709 con una LUT propia (el FFmpeg de Homebrew no trae zscale).
2. Corrección automática por toma: exposición, balance de blancos, contraste, saturación, nitidez.
3. Igualación entre tomas (todas apuntan a la misma luminancia objetivo) y LUT creativa opcional.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .util import dir_cache, log

# BT.2020 → BT.709 en luz lineal
_M2020_709 = np.array([
    [1.6605, -0.5876, -0.0728],
    [-0.1246, 1.1329, -0.0083],
    [-0.0182, -0.1006, 1.1187],
])


def _hlg_inversa(e: np.ndarray) -> np.ndarray:
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    return np.where(e <= 0.5, (e ** 2) / 3.0, (np.exp((e - c) / a) + b) / 12.0)


def _curva_hombro(x: np.ndarray, rodilla: float = 0.72) -> np.ndarray:
    """Lineal hasta la rodilla y compresión suave de las altas luces hacia 1.0."""
    k = rodilla
    return np.where(x <= k, x, k + (1 - k) * (1 - np.exp(-(x - k) / (1 - k))))


def lut_hlg_a_sdr(tam: int = 33, blanco: float = 0.62) -> Path:
    """Genera (una vez) la LUT .cube HLG BT.2020 → SDR BT.709.

    blanco = a qué nivel lineal SDR se lleva el blanco de referencia HLG (203 nits, señal 75 %).
    """
    luts = dir_cache() / "luts"                      # nunca dentro del paquete
    luts.mkdir(parents=True, exist_ok=True)
    ruta = luts / f"hlg_a_sdr709_{tam}_{int(blanco * 100)}.cube"
    if ruta.exists():
        return ruta
    log("Generando LUT HDR(HLG) → SDR …")
    g = np.linspace(0, 1, tam)
    # .cube: R varía más rápido, luego G, luego B
    B, G, R = np.meshgrid(g, g, g, indexing="ij")
    rgb = np.stack([R, G, B], axis=-1).reshape(-1, 3)
    escena = _hlg_inversa(rgb)                                     # luz de escena 0..1
    ys = escena @ np.array([0.2627, 0.6780, 0.0593])
    pantalla = escena * np.power(np.maximum(ys, 1e-6), 0.2)[:, None]  # OOTF HLG (gamma 1.2, 1000 nits)
    rel = pantalla * (1000.0 / 203.0)                               # 1.0 = blanco de referencia
    lin709 = np.clip(rel @ _M2020_709.T, 0, None)
    y = lin709 @ np.array([0.2126, 0.7152, 0.0722])
    y_map = _curva_hombro(y * blanco)
    escala = np.where(y > 1e-6, y_map / np.maximum(y, 1e-6), 0)
    out = np.clip(lin709 * escala[:, None], 0, 1)
    out = np.power(out, 1 / 2.4)                                    # BT.1886
    lineas = [f"TITLE \"HLG a SDR709\"", f"LUT_3D_SIZE {tam}"]
    lineas += [f"{r:.6f} {g_:.6f} {b:.6f}" for r, g_, b in out]
    ruta.write_text("\n".join(lineas) + "\n")
    return ruta


def cadena_sdr(info: dict, blanco: float = 0.62) -> str:
    """Filtros que llevan cualquier fuente a SDR BT.709 yuv420p (se usa después de escalar).

    blanco: brillo del blanco HDR al pasarlo a SDR (más bajo = más contraste y color; 0.62 probado en iPhone).
    """
    if info.get("hlg"):
        lut = lut_hlg_a_sdr(blanco=blanco)
        return (
            "format=yuv420p10le,"
            "scale=in_color_matrix=bt2020:in_range=tv,format=gbrp16le,"
            f"lut3d=file='{lut}':interp=tetrahedral,"
            "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
        )
    if info.get("hdr"):
        log(f"Aviso: {info['ruta']} es HDR PQ; se trata como HLG aproximado.")
        return cadena_sdr({**info, "hlg": True}, blanco)
    return "format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


def medir(frames: list[np.ndarray]) -> dict:
    """Estadísticas de color de fotogramas BGR (OpenCV) ya en SDR."""
    if not frames:
        return {}
    arr = np.stack([f.astype(np.float32) / 255.0 for f in frames])
    b, g, r = arr[..., 0].mean(), arr[..., 1].mean(), arr[..., 2].mean()
    luma = 0.2126 * arr[..., 2] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 0]
    mx, mn = arr.max(-1), arr.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0).mean()
    return {
        "luma": float(luma.mean()),
        "p02": float(np.percentile(luma, 2)),
        "p98": float(np.percentile(luma, 98)),
        "r": float(r), "g": float(g), "b": float(b),
        "sat": float(sat),
    }


def cadena_correccion(stats: dict, cfg: dict) -> str:
    """Filtros de corrección automática a partir de las estadísticas de la toma."""
    c = cfg.get("color", {})
    if not c.get("auto", True) or not stats:
        return _creativo(c)
    filtros = []
    # Niveles: estira el rango útil sin quemar (contraste)
    if c.get("niveles", True):
        lo = min(max(stats["p02"] - 0.01, 0.0), 0.12)
        hi = max(min(stats["p98"] + 0.01, 1.0), 0.80)
        filtros.append(f"colorlevels=rimin={lo:.3f}:gimin={lo:.3f}:bimin={lo:.3f}:"
                       f"rimax={hi:.3f}:gimax={hi:.3f}:bimax={hi:.3f}")
        luma = (stats["luma"] - lo) / max(hi - lo, 1e-3)
    else:
        luma = stats["luma"]
    # Balance de blancos (mundo gris parcial: conserva algo del tono original)
    fuerza = float(c.get("balance_blancos", 0.6))
    if fuerza > 0:
        gris = (stats["r"] + stats["g"] + stats["b"]) / 3
        gan = [min(max(1 + fuerza * (gris / max(v, 1e-3) - 1), 0.85), 1.18)
               for v in (stats["r"], stats["g"], stats["b"])]
        filtros.append(f"colorchannelmixer=rr={gan[0]:.3f}:gg={gan[1]:.3f}:bb={gan[2]:.3f}")
    # Exposición: gamma hacia la luminancia objetivo
    objetivo = float(c.get("luma_objetivo", 0.46))
    luma = min(max(luma, 0.05), 0.95)
    gamma = float(np.log(luma) / np.log(objetivo))
    gamma = min(max(gamma, float(c.get("gamma_min", 0.75))), float(c.get("gamma_max", 1.6)))
    sat = float(c.get("saturacion", 1.12))
    if stats.get("sat", 0.3) < 0.18:
        sat += 0.08                                            # imagen apagada → un poco más de color
    filtros.append(f"eq=gamma={gamma:.3f}:saturation={sat:.3f}:contrast={float(c.get('contraste', 1.04)):.3f}")
    return ",".join(filtros + ([_creativo(c)] if _creativo(c) else []))


def _creativo(c: dict) -> str:
    f = []
    if c.get("vibrance"):
        f.append(f"vibrance=intensity={float(c['vibrance']):.2f}")
    if c.get("temperatura"):
        f.append(f"colortemperature=temperature={int(c['temperatura'])}:mix=0.35")
    if c.get("lut"):
        f.append(f"lut3d=file='{Path(c['lut']).expanduser()}'")
    if c.get("nitidez", 0):
        f.append(f"cas=strength={float(c['nitidez']):.2f}")
    if c.get("vineta"):
        f.append("vignette=angle=PI/5")
    return ",".join(f)
