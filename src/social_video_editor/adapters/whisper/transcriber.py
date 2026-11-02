"""Transcriber: word-level transcription with faster-whisper in a subprocess.

OpenCV and PyAV ship libraries that clash when loaded in the same process, so Whisper runs apart.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ...log import log

PACKAGE_ROOT = Path(__file__).resolve().parents[3]       # folder that contains social_video_editor/


class WhisperTranscriber:
    def transcribe(self, wav: str, settings: dict, workdir: Path) -> list[dict]:
        """settings: {"model", "language", "context", "min_probability"} → [{"word", "start", "end", "prob"}]"""
        cfg_p, out_p = workdir / "_whisper_config.json", workdir / (Path(wav).stem + "_words.json")
        cfg_p.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        # the subprocess finds the package even when it is not installed
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(x for x in (str(PACKAGE_ROOT), os.environ.get("PYTHONPATH")) if x)}
        r = subprocess.run([sys.executable, "-m", "social_video_editor.adapters.whisper.worker", wav, str(cfg_p), str(out_p)],
                           capture_output=True, text=True, env=env)
        if r.returncode != 0:
            log("Warning: transcription failed; continuing without automatic subtitles.\n" + r.stderr[-800:])
            return []
        return json.loads(out_p.read_text(encoding="utf-8"))
