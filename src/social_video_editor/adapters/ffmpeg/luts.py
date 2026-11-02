"""The HLG → SDR .cube LUT file (generated once in the user cache, never inside the package)."""
from __future__ import annotations

from pathlib import Path

from ...domain.color_math import hlg_to_sdr_lut
from ...log import log
from ..system.tools import user_cache_dir


def hlg_lut_path(size: int = 33, white: float = 0.62) -> Path:
    luts = user_cache_dir() / "luts"
    luts.mkdir(parents=True, exist_ok=True)
    path = luts / f"hlg_to_sdr709_{size}_{int(white * 100)}.cube"
    if path.exists():
        return path
    log("Generating HDR (HLG) → SDR LUT…")
    rows = hlg_to_sdr_lut(size, white)
    lines = ['TITLE "HLG to SDR709"', f"LUT_3D_SIZE {size}"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in rows]
    path.write_text("\n".join(lines) + "\n")
    return path
