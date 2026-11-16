"""Small HTTP helpers for the footage sources (standard library only). Errors become FootageError
with a message that never contains the request URL: an API key may travel in it."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ... import __version__
from ...domain.footage import FootageError
from ..files.atomic import publish, temp_name

USER_AGENT = f"social-video-editor/{__version__} (https://github.com/MetadHInd/social-video-editor)"
CHUNK = 1 << 16
TOTAL_SECONDS = 180          # a whole download never takes longer than this
STATUS = {401: "the API key was not accepted", 403: "access refused (check the API key)",
          429: "too many requests for now (rate limit); try again later"}


def _open(url: str, headers: dict | None, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        return urllib.request.urlopen(request, timeout=timeout)     # noqa: S310  (https URLs built by the adapters)
    except urllib.error.HTTPError as e:
        raise FootageError(STATUS.get(e.code, f"the server answered {e.code}")) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise FootageError(f"no connection ({reason})") from None


def get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: float = 30):
    """GET `url` with query `params` → the decoded JSON answer."""
    if params:
        url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    with _open(url, headers, timeout) as response:
        try:
            return json.loads(response.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise FootageError("the server's answer was not JSON") from None


def fetch(url: str, dest: Path, headers: dict | None = None, max_bytes: int | None = None, timeout: float = 60,
          total: float = TOTAL_SECONDS) -> Path:
    """Downloads `url` to `dest` (written under a temporary name, published when complete). A file
    bigger than max_bytes is abandoned, and so is a download still going after `total` seconds
    (`timeout` only bounds each read: a server that trickles would otherwise hold it for ages)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(dest)
    try:
        deadline = time.monotonic() + total
        with _open(url, headers, timeout) as response, tmp.open("wb") as out:
            size = 0
            while True:
                if time.monotonic() > deadline:
                    raise FootageError(f"the download took longer than {total:g} s")
                block = response.read(CHUNK)
                if not block:
                    break
                size += len(block)
                if max_bytes and size > max_bytes:
                    raise FootageError(f"the file is larger than {max_bytes // (1 << 20)} MB (broll.max_megabytes)")
                out.write(block)
        if size == 0:
            raise FootageError("the download was empty")
        return publish(tmp, dest)
    except (TimeoutError, OSError) as e:
        raise FootageError(f"the download failed ({e})") from None
    finally:
        tmp.unlink(missing_ok=True)


def suffix_of(url: str, default: str = ".mp4") -> str:
    """The file extension a URL ends with (.mp4, .webm, .mov…)."""
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return ext if ext in (".mp4", ".webm", ".mov", ".m4v", ".ogv", ".mkv") else default
