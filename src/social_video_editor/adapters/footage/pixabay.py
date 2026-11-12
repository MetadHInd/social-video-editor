"""FootageSource: Pixabay videos (free stock; https://pixabay.com/api/docs/).

Needs a free API key in the environment variable PIXABAY_API_KEY (never in a file). Pixabay asks that
search answers be cached for 24 hours and files be downloaded, not hot-linked: the use case caches
the searches and every clip is copied into the project."""
from __future__ import annotations

import os
from pathlib import Path

from ...domain.footage import STOCK, FootageError
from . import http

API = "https://pixabay.com/api/videos/"
KEY_ENV = "PIXABAY_API_KEY"
LANGUAGES = {"cs", "da", "de", "en", "es", "fr", "id", "it", "hu", "nl", "no", "pl", "pt", "ro", "sk", "fi", "sv",
             "tr", "vi", "th", "bg", "ru", "el", "ja", "ko", "zh"}
ENOUGH = 1080


def pick_file(videos: dict) -> dict | None:
    """The rendition to download: the smallest whose short side reaches ENOUGH, else the biggest."""
    usable = [v for v in (videos or {}).values() if isinstance(v, dict) and v.get("url") and v.get("width") and v.get("height")]
    if not usable:
        return None
    usable.sort(key=lambda v: min(v["width"], v["height"]))
    return next((v for v in usable if min(v["width"], v["height"]) >= ENOUGH), usable[-1])


class PixabaySource:
    name, kind = "pixabay", STOCK

    def __init__(self, max_megabytes: int = 250):
        self.max_bytes = max_megabytes << 20

    def ready(self) -> str | None:
        return None if os.environ.get(KEY_ENV) else f"{KEY_ENV} is not set (free key: https://pixabay.com/api/docs/)"

    def search(self, query: str, limit: int, orientation: str, language: str = "en", brand: dict | None = None) -> list[dict]:
        key = os.environ.get(KEY_ENV)
        if not key:
            raise FootageError(self.ready())
        data = http.get_json(API, {"key": key, "q": query[:100], "per_page": max(3, min(int(limit), 200)),
                                   "safesearch": "true", "lang": language if language in LANGUAGES else "en"})
        out = []
        for v in data.get("hits") or []:
            f = pick_file(v.get("videos"))
            if f is None:
                continue
            out.append({"source": self.name, "kind": self.kind, "id": str(v.get("id")), "title": str(v.get("tags") or ""),
                        "query": query, "url": f["url"], "page": v.get("pageURL"), "author": str(v.get("user") or ""),
                        "license": "Pixabay Content License", "license_code": "pixabay",
                        "license_url": "https://pixabay.com/service/license-summary/", "width": int(f["width"]),
                        "height": int(f["height"]), "duration": float(v.get("duration") or 0)})
        return out

    def download(self, candidate: dict, dest: Path, piece: tuple[float, float] | None = None,
                 preview: bool = False) -> Path:
        return http.fetch(candidate["url"], Path(dest).with_suffix(http.suffix_of(candidate["url"])),
                          max_bytes=self.max_bytes)
