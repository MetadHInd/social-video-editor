"""MusicDownloader with yt-dlp (first YouTube result, or a URL) → mp3."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ...domain.models import UserError


class YtDlpDownloader:
    def download(self, query: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        target = query if query.startswith("http") else f"ytsearch1:{query}"
        try:
            r = subprocess.run(["yt-dlp", target, "-x", "--audio-format", "mp3", "--audio-quality", "0",
                                "--no-playlist", "--print", "after_move:filepath", "-o", str(dest / "%(title)s.%(ext)s")],
                               capture_output=True, text=True)
        except FileNotFoundError:
            raise UserError("yt-dlp is not installed (pip install 'social-video-editor[music]')") from None
        if r.returncode != 0:
            raise UserError(r.stderr[-800:])
        return Path(r.stdout.strip().splitlines()[-1])
