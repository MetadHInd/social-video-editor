"""FootageSource: Wikimedia Commons videos (no key). Every file there has its own license: the answer
carries it and domain/footage.py only accepts the open ones (CC0, public domain, CC BY, CC BY-SA),
with the author recorded for the attribution those licenses ask for.

API: https://commons.wikimedia.org/w/api.php (action=query, generator=search in the File namespace,
prop=videoinfo with the file's renditions and its license metadata)."""
from __future__ import annotations

from pathlib import Path

from ...domain.footage import STOCK, license_code, plain
from . import http

API = "https://commons.wikimedia.org/w/api.php"
ENOUGH = 1080
MAX_AUTHOR = 80
THUMB_WIDTH = 480           # the still of each video asked for, to look at it before downloading


def pick_file(info: dict, max_bytes: int) -> dict | None:
    """{"url", "width", "height"} of the rendition to download: the original when it is not bigger than
    full HD and not too heavy; otherwise the largest transcoded copy up to full HD whose size (its bit
    rate × the duration) stays under max_bytes."""
    w, h = int(info.get("width") or 0), int(info.get("height") or 0)
    if info.get("url") and w and h and min(w, h) <= ENOUGH and int(info.get("size") or 0) <= max_bytes:
        return {"url": info["url"], "width": w, "height": h}
    seconds = float(info.get("duration") or 0)
    copies = [d for d in info.get("derivatives") or []
              if d.get("src") and d.get("width") and d.get("height") and "webm" in str(d.get("type") or "")
              and min(int(d["width"]), int(d["height"])) <= ENOUGH
              and float(d.get("bandwidth") or 0) * seconds / 8 <= max_bytes]
    if not copies:
        return None
    best = max(copies, key=lambda d: int(d["width"]) * int(d["height"]))
    return {"url": best["src"], "width": int(best["width"]), "height": int(best["height"])}


class WikimediaSource:
    name, kind = "wikimedia", STOCK

    def __init__(self, max_megabytes: int = 250):
        self.max_bytes = max_megabytes << 20

    def ready(self) -> str | None:
        return None

    def search(self, query: str, limit: int, orientation: str, language: str = "en", brand: dict | None = None) -> list[dict]:
        data = http.get_json(API, {
            "action": "query", "format": "json", "formatversion": 2, "generator": "search",
            "gsrsearch": f"filetype:video {query}", "gsrnamespace": 6, "gsrlimit": max(1, min(int(limit), 50)),
            "prop": "videoinfo", "viprop": "url|size|mime|derivatives|extmetadata", "viurlwidth": THUMB_WIDTH})
        pages = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
        out = []
        for p in pages:
            info = (p.get("videoinfo") or [{}])[0]
            f = pick_file(info, self.max_bytes)
            if f is None:
                continue
            meta = {k: (v or {}).get("value") for k, v in (info.get("extmetadata") or {}).items()}
            name = str(p.get("title") or "").removeprefix("File:")
            shown = plain(meta.get("LicenseShortName")) or plain(meta.get("License"))
            out.append({"source": self.name, "kind": self.kind, "id": str(p.get("pageid") or name),
                        "title": plain(meta.get("ObjectName")) or Path(name).stem, "query": query, "url": f["url"],
                        "thumb": info.get("thumburl"),
                        "page": info.get("descriptionurl"), "author": plain(meta.get("Artist"))[:MAX_AUTHOR],
                        "license": shown, "license_code": license_code(meta.get("License")) or license_code(shown),
                        "license_url": meta.get("LicenseUrl"), "width": f["width"], "height": f["height"],
                        "duration": float(info.get("duration") or 0)})
        return out

    def preview(self, candidate: dict, dest: Path) -> Path | None:
        return http.fetch(candidate["thumb"], Path(dest).with_suffix(".jpg"), max_bytes=5 << 20) if candidate.get("thumb") else None

    def download(self, candidate: dict, dest: Path, piece: tuple[float, float] | None = None,
                 preview: bool = False) -> Path:
        return http.fetch(candidate["url"], Path(dest).with_suffix(http.suffix_of(candidate["url"], ".webm")),
                          max_bytes=self.max_bytes)
