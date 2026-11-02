"""Per-project cache of probes and analyses (cache/ inside the project folder).

New files use English names and keys; legacy Spanish cache files (<id>_info.json,
<id>_analisis.json, musica_<song>.json…) are still read (translated) and never modified.
"""
from __future__ import annotations

from pathlib import Path

from . import legacy_es
from .jsonio import read_json, write_json


class JsonCacheStore:
    def load(self, cache_dir: Path, kind: str, key: str, newer_than: Path | None = None):
        new, old, ctx = legacy_es.CACHE_FILES[kind]
        for name, legacy in ((new, False), (old, True)):
            p = Path(cache_dir) / name.format(key=key)
            if p.exists() and (newer_than is None or p.stat().st_mtime > Path(newer_than).stat().st_mtime):
                data = read_json(p)
                return legacy_es.translate(data, ctx) if legacy else data
        return None

    def save(self, cache_dir: Path, kind: str, key: str, data) -> Path:
        p = Path(cache_dir) / legacy_es.CACHE_FILES[kind][0].format(key=key)
        write_json(p, data)
        return p

    def file(self, cache_dir: Path, name: str) -> Path:
        p = Path(cache_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def has(self, path: Path) -> bool:
        return Path(path).exists()

    def discard(self, path: Path) -> None:
        Path(path).unlink(missing_ok=True)
