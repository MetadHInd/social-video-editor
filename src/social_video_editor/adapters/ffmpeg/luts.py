"""The HLG → SDR .cube LUT file (generated once in the user cache, never inside the package)."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from ...domain.color_math import hlg_to_sdr_lut
from ...log import log
from ..system.tools import user_cache_dir

# shots may render in parallel threads: only one of them generates a missing LUT (check-then-write)
_LOCK = threading.Lock()


def hlg_lut_path(size: int = 33, white: float = 0.62) -> Path:
    luts = user_cache_dir() / "luts"
    luts.mkdir(parents=True, exist_ok=True)
    path = luts / f"hlg_to_sdr709_{size}_{int(white * 100)}.cube"
    with _LOCK:
        if path.exists():
            return path
        log("Generating HDR (HLG) → SDR LUT…")
        rows = hlg_to_sdr_lut(size, white)
        lines = ['TITLE "HLG to SDR709"', f"LUT_3D_SIZE {size}"]
        lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in rows]
        # unique temporary name + atomic replace: another process (a second `sve` run) never reads a
        # half-written LUT, and two writers never share one temporary file
        tmp = path.with_name(f"{path.stem}.{os.getpid()}-{threading.get_ident()}.tmp")
        try:
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            try:
                os.replace(tmp, path)
            except PermissionError:         # Windows: another process wrote the same LUT and FFmpeg has
                if not path.exists():       # it open; its content is identical, keep that one
                    raise
        finally:
            tmp.unlink(missing_ok=True)
    return path
