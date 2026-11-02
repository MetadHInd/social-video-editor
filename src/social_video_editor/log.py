"""Progress messages (always English)."""
from __future__ import annotations

import time


def log(msg: str) -> None:
    print(f"[sve {time.strftime('%H:%M:%S')}] {msg}", flush=True)
