"""FootageSource: Pexels videos (free stock; https://www.pexels.com/api/documentation/).

Needs a free API key in the environment variable PEXELS_API_KEY (never in a file). Pexels asks for a
visible credit to the author and to Pexels: the engine records both and shows them."""
from __future__ import annotations

import os
from pathlib import Path

from ...domain.footage import STOCK, FootageError
from . import http

API = "https://api.pexels.com/v1/videos/search"
KEY_ENV = "PEXELS_API_KEY"
LOCALES = {"es": "es-ES", "en": "en-US", "pt": "pt-BR", "fr": "fr-FR", "de": "de-DE", "it": "it-IT"}
ENOUGH = 1080           # short side wanted: the smallest file with at least this is taken


def pick_file(files: list[dict]) -> dict | None:
    """The MP4 rendition to download: the smallest whose short side reaches ENOUGH, else the biggest."""
    usable = [f for f in files or [] if f.get("link") and "mp4" in str(f.get("file_type") or "mp4")
              and f.get("width") and f.get("height")]
    if not usable:
        return None
    usable.sort(key=lambda f: min(f["width"], f["height"]))
    return next((f for f in usable if min(f["width"], f["height"]) >= ENOUGH), usable[-1])


class PexelsSource:
    name, kind = "pexels", STOCK

    def __init__(self, max_megabytes: int = 250):
        self.max_bytes = max_megabytes << 20

    def ready(self) -> str | None:
        return None if os.environ.get(KEY_ENV) else f"{KEY_ENV} is not set (free key: https://www.pexels.com/api/)"

    def search(self, query: str, limit: int, orientation: str, language: str = "en", brand: dict | None = None) -> list[dict]:
        key = os.environ.get(KEY_ENV)
        if not key:
            raise FootageError(self.ready())
        data = http.get_json(API, {"query": query, "per_page": max(1, min(int(limit), 80)), "orientation": orientation,
                                   "locale": LOCALES.get(language)}, {"Authorization": key})
        out = []
        for v in data.get("videos") or []:
            f = pick_file(v.get("video_files"))
            if f is None:
                continue
            user = v.get("user") or {}
            out.append({"source": self.name, "kind": self.kind, "id": str(v.get("id")), "title": "", "query": query,
                        "url": f["link"], "thumb": v.get("image"), "page": v.get("url"), "author": user.get("name") or "",
                        "author_url": user.get("url"), "license": "Pexels License", "license_code": "pexels",
                        "license_url": "https://www.pexels.com/license/", "width": int(f["width"]),
                        "height": int(f["height"]), "duration": float(v.get("duration") or 0)})
        return out

    def preview(self, candidate: dict, dest: Path) -> Path | None:
        return http.fetch(candidate["thumb"], Path(dest).with_suffix(".jpg"), max_bytes=5 << 20) if candidate.get("thumb") else None

    def download(self, candidate: dict, dest: Path, piece: tuple[float, float] | None = None,
                 preview: bool = False) -> Path:
        return http.fetch(candidate["url"], Path(dest).with_suffix(http.suffix_of(candidate["url"])),
                          max_bytes=self.max_bytes)
