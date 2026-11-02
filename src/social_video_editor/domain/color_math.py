"""Color math (numbers only; the FFmpeg adapter turns them into filters).

1. HDR (iPhone HLG) → SDR BT.709 3D LUT values (Homebrew's FFmpeg has no zscale).
2. Automatic per-shot correction from frame statistics: levels, white balance, exposure
   (gamma towards a common target luma, so all shots match), saturation, contrast.
3. Optional creative look: vibrance, temperature, LUT, sharpening, vignette.
"""
from __future__ import annotations

import numpy as np

# BT.2020 → BT.709 in linear light
_M2020_709 = np.array([
    [1.6605, -0.5876, -0.0728],
    [-0.1246, 1.1329, -0.0083],
    [-0.0182, -0.1006, 1.1187],
])


def _hlg_inverse(e: np.ndarray) -> np.ndarray:
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    return np.where(e <= 0.5, (e ** 2) / 3.0, (np.exp((e - c) / a) + b) / 12.0)


def _shoulder(x: np.ndarray, knee: float = 0.72) -> np.ndarray:
    """Linear up to the knee, then a soft roll-off of the highlights towards 1.0."""
    k = knee
    return np.where(x <= k, x, k + (1 - k) * (1 - np.exp(-(x - k) / (1 - k))))


def hlg_to_sdr_lut(size: int = 33, white: float = 0.62) -> np.ndarray:
    """RGB rows of a .cube LUT HLG BT.2020 → SDR BT.709 (R varies fastest, then G, then B).

    white = linear SDR level the HLG reference white (203 nits, 75 % signal) is mapped to.
    """
    g = np.linspace(0, 1, size)
    B, G, R = np.meshgrid(g, g, g, indexing="ij")
    rgb = np.stack([R, G, B], axis=-1).reshape(-1, 3)
    scene = _hlg_inverse(rgb)                                       # scene light 0..1
    ys = scene @ np.array([0.2627, 0.6780, 0.0593])
    display = scene * np.power(np.maximum(ys, 1e-6), 0.2)[:, None]  # HLG OOTF (gamma 1.2, 1000 nits)
    rel = display * (1000.0 / 203.0)                                # 1.0 = reference white
    lin709 = np.clip(rel @ _M2020_709.T, 0, None)
    y = lin709 @ np.array([0.2126, 0.7152, 0.0722])
    y_map = _shoulder(y * white)
    scale = np.where(y > 1e-6, y_map / np.maximum(y, 1e-6), 0)
    out = np.clip(lin709 * scale[:, None], 0, 1)
    return np.power(out, 1 / 2.4)                                   # BT.1886


def measure(frames: list[np.ndarray]) -> dict:
    """Color statistics of SDR BGR frames (uint8 arrays)."""
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


def correction(stats: dict, color_cfg: dict) -> dict | None:
    """Automatic correction parameters, or None when disabled / no statistics.

    {"levels": (lo, hi) | None, "gains": (r, g, b) | None, "gamma", "saturation", "contrast"}
    """
    c = color_cfg
    if not c.get("auto", True) or not stats:
        return None
    out: dict = {"levels": None, "gains": None}
    if c.get("levels", True):                     # stretch the useful range without clipping
        lo = min(max(stats["p02"] - 0.01, 0.0), 0.12)
        hi = max(min(stats["p98"] + 0.01, 1.0), 0.80)
        out["levels"] = (lo, hi)
        luma = (stats["luma"] - lo) / max(hi - lo, 1e-3)
    else:
        luma = stats["luma"]
    strength = float(c.get("white_balance", 0.6))
    if strength > 0:                              # partial gray world: keeps some of the original tone
        gray = (stats["r"] + stats["g"] + stats["b"]) / 3
        out["gains"] = tuple(min(max(1 + strength * (gray / max(v, 1e-3) - 1), 0.85), 1.18)
                             for v in (stats["r"], stats["g"], stats["b"]))
    target = float(c.get("target_luma", 0.46))    # exposure: gamma towards the target luma
    luma = min(max(luma, 0.05), 0.95)
    gamma = float(np.log(luma) / np.log(target))
    out["gamma"] = min(max(gamma, float(c.get("gamma_min", 0.75))), float(c.get("gamma_max", 1.6)))
    sat = float(c.get("saturation", 1.12))
    if stats.get("sat", 0.3) < 0.18:
        sat += 0.08                               # dull image → a bit more color
    out["saturation"] = sat
    out["contrast"] = float(c.get("contrast", 1.04))
    return out


def creative(color_cfg: dict) -> dict:
    """Optional look applied after the correction (only the enabled parts)."""
    c = color_cfg
    out = {}
    if c.get("vibrance"):
        out["vibrance"] = float(c["vibrance"])
    if c.get("temperature"):
        out["temperature"] = int(c["temperature"])
    if c.get("lut"):
        out["lut"] = str(c["lut"])
    if c.get("sharpen", 0):
        out["sharpen"] = float(c["sharpen"])
    if c.get("vignette"):
        out["vignette"] = True
    return out
