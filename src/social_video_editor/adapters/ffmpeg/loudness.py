"""ONE loudness measurement (EBU R128 / ITU-R BS.1770 via FFmpeg's ebur128 filter), shared by the
mastering (encoder.py) and by `sve check` (inspector.py) so both always agree.

ebur128 gives the gated integrated loudness of the whole file and the 4x-oversampled true peak.
(loudnorm's print-out "input_i" is not used: on short files it over-weights the last seconds.)
"""
from __future__ import annotations

import re
from pathlib import Path

from .runner import stderr_of

_FRAME = re.compile(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+|-inf|nan)\s+S:\s*(-?[\d.]+|-inf|nan)")
_NUM = r"(-?[\d.]+|-inf|nan)"


def _f(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return float("-inf")


def ebur128(path: str | Path, series: bool = False) -> dict:
    """{"integrated" (LUFS), "true_peak" (dBTP), "lra" (LU)} of the first audio stream.

    series=True also returns "t", "momentary", "short_term" (one value every 0.1 s).
    Silence gives −inf / −70 rather than an error.
    """
    err = stderr_of(["-nostats", "-i", str(path), "-map", "0:a:0", "-af",
                     f"ebur128=peak=true:framelog={'info' if series else 'verbose'}", "-f", "null", "-"])
    summary = err[err.rfind("Summary:"):]

    def get(pat: str) -> float:
        m = re.search(pat, summary)
        return _f(m.group(1)) if m else float("-inf")

    out = {"integrated": get(rf"I:\s*{_NUM} LUFS"), "lra": get(rf"LRA:\s*{_NUM} LU"),
           "true_peak": get(rf"Peak:\s*{_NUM} dBFS")}
    if series:
        t, m, s = [], [], []
        for mt in _FRAME.finditer(err):
            t.append(float(mt.group(1)))
            m.append(_f(mt.group(2)))
            s.append(_f(mt.group(3)))
        out.update(t=t, momentary=m, short_term=s)
    return out
