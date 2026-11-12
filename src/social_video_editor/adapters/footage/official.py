"""FootageSource: the official channels of the brands the user lists (`broll.brands`, or a pack's
brands.yaml), read with yt-dlp.

Only what a listed channel published itself is ever used: the search runs inside that channel, every
result must carry its channel id, and the download is refused by yt-dlp itself (`--match-filter`)
when the video belongs to any other channel. Only the piece that is needed is downloaded in full
quality (a small preview is used to choose it and deleted afterwards); no sound is downloaded."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
from pathlib import Path

from ...domain.footage import OFFICIAL, FootageError
from ..ytdlp.downloader import INSTALL_HINT, ytdlp_command

SEARCH_URL = "https://www.youtube.com/channel/{channel}/search?query={query}"
PREVIEW_FORMAT = "bv*[height<=360][ext=mp4]/bv*[height<=360]/wv*"     # big enough to read letters and see the subject
PIECE_FORMAT = "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/b[height<=1080]"
TIMEOUT = 600


def _run(args: list[str]) -> str:
    cmd = ytdlp_command()
    if cmd is None:
        raise FootageError(INSTALL_HINT)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run([*cmd, "--no-warnings", *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=TIMEOUT)
    except FileNotFoundError:
        raise FootageError(INSTALL_HINT) from None
    except subprocess.TimeoutExpired:
        raise FootageError("yt-dlp took too long") from None
    if r.returncode != 0:
        raise FootageError((r.stderr or "yt-dlp failed").strip().splitlines()[-1][:300])
    return r.stdout


def search_url(channel_id: str, query: str) -> str:
    return SEARCH_URL.format(channel=channel_id, query=urllib.parse.quote(query))


def candidates(listing: dict, brand: dict, query: str) -> list[dict]:
    """The videos of a channel search that belong to the brand's channel (anything else is dropped)."""
    out = []
    for e in listing.get("entries") or []:
        if not isinstance(e, dict) or not e.get("id") or e.get("channel_id") != brand["channel_id"]:
            continue
        page = e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"
        out.append({"source": "official", "kind": OFFICIAL, "id": str(e["id"]), "title": str(e.get("title") or ""),
                    "query": query, "url": page, "page": page, "author": str(e.get("channel") or brand["name"]),
                    "brand": brand["name"], "channel_id": e["channel_id"],
                    "license": f"© {brand['name']} (official channel)", "license_code": "official",
                    "width": None, "height": None, "duration": float(e.get("duration") or 0)})
    return out


class OfficialChannelSource:
    name, kind = "official", OFFICIAL

    def ready(self) -> str | None:
        return None if ytdlp_command() else INSTALL_HINT

    def search(self, query: str, limit: int, orientation: str, language: str = "en", brand: dict | None = None) -> list[dict]:
        if not brand:
            return []
        text = _run(["--flat-playlist", "--playlist-end", str(max(1, int(limit))), "-J",
                     search_url(brand["channel_id"], query)])
        try:
            listing = json.loads(text)
        except ValueError:
            raise FootageError("yt-dlp's answer was not JSON") from None
        return candidates(listing, brand, query)

    def download(self, candidate: dict, dest: Path, piece: tuple[float, float] | None = None,
                 preview: bool = False) -> Path:
        channel = candidate.get("channel_id")
        if not channel:
            raise FootageError("an official clip without its channel id is never downloaded")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["--no-playlist", "--match-filter", f"channel_id={channel}",
                "-f", PREVIEW_FORMAT if preview else PIECE_FORMAT,
                "--print", "after_move:filepath", "-o", str(dest.with_suffix("")) + ".%(ext)s"]
        if piece and not preview:
            args += ["--download-sections", f"*{max(piece[0], 0):.2f}-{piece[1]:.2f}", "--force-keyframes-at-cuts"]
        lines = [x for x in _run([*args, candidate["url"]]).strip().splitlines() if x.strip()]
        path = Path(lines[-1]) if lines else None
        if path is None or not path.is_file():
            raise FootageError("nothing was downloaded (the video is not from the listed channel, or it is unavailable)")
        return path
