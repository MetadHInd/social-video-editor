"""MusicDownloader with yt-dlp (first YouTube result, or a URL) → mp3."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ...domain.models import UserError

INSTALL_HINT = "yt-dlp is not installed (pip install 'social-video-editor[music]')"


def ytdlp_command() -> list[str] | None:
    """How to run yt-dlp: the module installed in this Python (pip extra; works even when its Scripts/
    folder is not on PATH, common on Windows), else a yt-dlp program on PATH; None when there is neither.
    find_spec does not import yt_dlp."""
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    return [exe] if exe else None


class YtDlpDownloader:
    def download(self, query: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        target = query if query.startswith("http") else f"ytsearch1:{query}"
        cmd = ytdlp_command()
        if cmd is None:
            raise UserError(INSTALL_HINT)
        # UTF-8 both ways: song titles with accents or emoji survive the Windows code page
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            r = subprocess.run([*cmd, target, "-x", "--audio-format", "mp3", "--audio-quality", "0",
                                "--no-playlist", "--print", "after_move:filepath", "-o", str(dest / "%(title)s.%(ext)s")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        except FileNotFoundError:
            raise UserError(INSTALL_HINT) from None
        if r.returncode != 0:
            raise UserError(r.stderr[-800:])
        return Path(r.stdout.strip().splitlines()[-1])
