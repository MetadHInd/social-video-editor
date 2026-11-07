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
        "p05": float(np.percentile(luma, 5)),
        "p95": float(np.percentile(luma, 95)),
        "p98": float(np.percentile(luma, 98)),
        **_highlights(arr, luma),
        "r": float(r), "g": float(g), "b": float(b),
        "sat": float(sat),
    }


def _highlights(arr: np.ndarray, luma: np.ndarray) -> dict:
    """Mean color of the brightest 20 % of pixels (walls, white objects: the light's color, not the products')."""
    sel = luma >= np.percentile(luma, 80)
    if not sel.any():
        return {}
    return {"hr": float(arr[..., 2][sel].mean()), "hg": float(arr[..., 1][sel].mean()),
            "hb": float(arr[..., 0][sel].mean())}


def correction(stats: dict, color_cfg: dict) -> dict | None:
    """Automatic correction parameters, or None when disabled / no statistics.

    {"levels": (lo, hi) | None, "gains": (r, g, b) | None, "gamma", "saturation", "contrast"}
    """
    c = color_cfg
    if not c.get("auto", True) or not stats:
        return None
    if c.get("_reference"):
        return match_reference(stats, c["_reference"], float(c.get("reference_strength", 1.0)))
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


def match_reference(stats: dict, ref: dict, strength: float = 1.0) -> dict:
    """Correction that brings one shot to the look of a reference video (its measured statistics).

    Color cast → the reference's channel ratios in the highlights (gains); then gamma and a linear map ("linear":
    input/output black and white levels) chosen so that the shot's 5 % / 95 % brightness points and
    its mean land on the reference's (lifted blacks, soft whites, same exposure); saturation scaled to
    the reference's. strength 0…1 blends every parameter with "no change".
    """
    k = min(max(strength, 0.0), 1.0)
    ch = ("hr", "hg", "hb") if all(c in stats and c in ref for c in ("hr", "hg", "hb")) else ("r", "g", "b")
    s_mean = sum(stats[c] for c in ch) / 3       # the light's color: from the highlights when measured
    r_mean = sum(ref[c] for c in ch) / 3
    gains = tuple(1 + k * (min(max((ref[c] / max(r_mean, 1e-3)) / max(stats[c] / max(s_mean, 1e-3), 1e-3),
                                   0.9), 1.1) - 1) for c in ch)   # ±10 %: keeps product colors
    s05, s95 = stats.get("p05", stats["p02"]), stats.get("p95", stats["p98"])
    r05, r95 = ref.get("p05", ref["p02"]), ref.get("p95", ref["p98"])
    luma = min(max(stats["luma"], 0.03), 0.97)
    g, a, b = 1.0, 0.0, 1.0
    for _ in range(4):                            # gamma and linear map depend on each other: iterate
        p05, p95 = (max(s05, 1e-3) ** (1 / g), max(s95, 1e-3) ** (1 / g))
        b = (r95 - r05) / max(p95 - p05, 0.05)
        a = r05 - b * p05
        t = min(max((ref["luma"] - a) / max(b, 1e-3), 0.05), 0.95)
        g = min(max(float(np.log(luma) / np.log(t)), 0.6), 1.8)
    a, b, g = k * a, 1 + k * (b - 1), 1 + k * (g - 1)
    i_lo = max(-a / b, 0.0) if b > 0 else 0.0     # where the output reaches 0 / 1 (clipped there)
    i_hi = min((1 - a) / b, 1.0) if b > 0 else 1.0
    linear = (i_lo, i_hi, max(a + b * i_lo, 0.0), min(a + b * i_hi, 1.0))
    sat = 1 + k * (min(max(ref["sat"] / max(stats["sat"], 0.05) * (1 + 0.6 * max(a, 0)), 0.5), 1.6) - 1)
    return {"levels": None, "gains": gains, "gamma": g, "saturation": sat, "contrast": 1.0, "linear": linear}


def creative(color_cfg: dict) -> dict:
    """Optional look applied after the correction (only the enabled parts)."""
    c = color_cfg
    out = {}
    if c.get("vibrance"):
        out["vibrance"] = float(c["vibrance"])
    if c.get("temperature"):
        out["temperature"] = int(c["temperature"])
    if c.get("fade"):                    # soft "matte" look: lifted blacks, softer whites
        out["fade"] = min(max(float(c["fade"]), 0.0), 0.4)
    if c.get("tint"):                    # + = pink/magenta, - = green
        out["tint"] = min(max(float(c["tint"]), -1.0), 1.0)
    if c.get("lut"):
        out["lut"] = str(c["lut"])
    if c.get("sharpen", 0):
        out["sharpen"] = float(c["sharpen"])
    if c.get("vignette"):
        out["vignette"] = True
    return out
