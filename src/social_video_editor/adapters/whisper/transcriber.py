"""Transcriber: word-level transcription with faster-whisper in a subprocess, and the engine choice.

OpenCV and PyAV ship libraries that clash when loaded in the same process, so Whisper runs apart.

`transcription.engine` picks who runs Whisper (the words, times and filters are the same contract):
  * faster-whisper — CTranslate2 on the CPU (int8, 5-beam search): the engine of every earlier version;
  * mlx            — the same model size on the Apple GPU (adapters/mlx_whisper), optional;
  * auto           — mlx when it is installed and this is a Mac with Apple Silicon, else faster-whisper.
The default is faster-whisper: measured on an M1 Pro (scripts/bench.py --scenario analyze and the
CHANGELOG), mlx-whisper was not 2× faster and its greedy decoding changed ~9 % of the words of a real
recording, so it stays opt-in. An engine that fails falls back to faster-whisper (logged), so a broken optional part never costs a video
its subtitles.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from ...log import log

PACKAGE_ROOT = Path(__file__).resolve().parents[3]       # folder that contains social_video_editor/
NAME = "faster-whisper"
ENGINES = ("auto", "faster-whisper", "mlx")
ALIASES = {"faster_whisper": NAME, "fasterwhisper": NAME, "cpu": NAME, "ctranslate2": NAME,
           "mlx-whisper": "mlx", "mlx_whisper": "mlx", "apple": "mlx", "gpu": "mlx"}


def run_worker(module: str, wav: str, settings: dict, workdir: Path, engine: str = NAME) -> list[dict] | None:
    """Run `python -m <module> <wav> <config> <out>`; its words, or None when it failed (logged).

    The config and output files are named after the wav, so sources can be transcribed side by side."""
    stem = Path(wav).stem
    cfg_p, out_p = workdir / f"_whisper_{stem}.json", workdir / (stem + "_words.json")
    cfg_p.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    # the subprocess finds the package even when it is not installed
    # (UTF-8 both ways, so error messages survive the Windows code page)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": os.pathsep.join(x for x in (str(PACKAGE_ROOT), os.environ.get("PYTHONPATH")) if x)}
    try:
        r = subprocess.run([sys.executable, "-m", module, wav, str(cfg_p), str(out_p)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    except OSError as e:
        log(f"Warning: transcription ({engine}) could not start: {e}")
        return None
    if r.returncode != 0:
        log(f"Warning: transcription ({engine}) failed.\n" + r.stderr[-800:])
        return None
    try:
        words = json.loads(out_p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"Warning: transcription ({engine}) wrote no words: {e}")
        return None
    return words if isinstance(words, list) else None


class WhisperTranscriber:
    """faster-whisper (CTranslate2) on the CPU: the default engine."""
    name = NAME
    device = "CPU (int8)"

    def ready(self, settings: dict | None = None) -> str | None:
        return None if importlib.util.find_spec("faster_whisper") else "faster-whisper is not installed"

    def run(self, wav: str, settings: dict, workdir: Path) -> list[dict] | None:
        return run_worker("social_video_editor.adapters.whisper.worker", wav, settings, workdir, NAME)

    def transcribe(self, wav: str, settings: dict, workdir: Path) -> list[dict]:
        """settings: {"model", "language", "context", "min_probability"} → [{"word", "start", "end", "prob"}]"""
        words = self.run(wav, settings, workdir)
        if words is None:
            log("Warning: transcription failed; continuing without automatic subtitles.")
        return words or []


def engine_setting(settings: dict | None) -> str:
    """transcription.engine, normalized: auto | faster-whisper | mlx (missing = faster-whisper, the
    default; an unknown name is warned about and also means faster-whisper)."""
    raw = str((settings or {}).get("engine") or NAME).strip().lower()
    raw = ALIASES.get(raw, raw)
    if raw not in ENGINES:
        log(f"Warning: unknown transcription.engine {raw!r} (auto, faster-whisper or mlx); using {NAME}")
        return NAME
    return raw


class EngineTranscriber:
    """The Transcriber the engine uses: picks faster-whisper or an accelerated engine per
    `transcription.engine` and falls back to faster-whisper when the chosen one fails."""

    def __init__(self, default=None, accelerated: list | None = None):
        self.default = default or WhisperTranscriber()
        self.accelerated = list(accelerated or [])

    def engines(self) -> list:
        return [self.default, *self.accelerated]

    def choose(self, settings: dict | None):
        """The engine `settings` asks for: an explicit one that is not ready → faster-whisper (logged)."""
        wanted = engine_setting(settings)
        if wanted == self.default.name:
            return self.default
        for e in self.accelerated:
            if wanted in ("auto", e.name):
                why = e.ready(settings)
                if why is None:
                    return e
                if wanted == e.name:
                    log(f"Warning: transcription.engine: {e.name} cannot run here ({why}); using {self.default.name}")
        return self.default

    def transcribe_info(self, wav: str, settings: dict, workdir: Path) -> tuple[list[dict], dict]:
        """(words, {"engine", "model", "device"}) — "engine" names who actually transcribed."""
        engine = self.choose(settings)
        words = engine.run(wav, settings, workdir)
        if words is None and engine is not self.default:
            log(f"Warning: transcription with {engine.name} failed; transcribing with {self.default.name}")
            engine = self.default
            words = engine.run(wav, settings, workdir)
        if words is None:
            log("Warning: transcription failed; continuing without automatic subtitles.")
        return words or [], {"engine": engine.name, "model": str(settings.get("model") or "small"),
                             "device": engine.device}

    def transcribe(self, wav: str, settings: dict, workdir: Path) -> list[dict]:
        return self.transcribe_info(wav, settings, workdir)[0]
