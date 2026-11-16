"""Transcriber on the Apple GPU: the same Whisper models through mlx-whisper, in a subprocess.

Optional (`pip install "social-video-editor[apple]"`, Apple Silicon only). Nothing heavy is imported
here: `ready()` only looks the packages up. The model of each size is downloaded once from the
Hugging Face hub (mlx-community/whisper-<size>-mlx), unless SVE_OFFLINE / HF_HUB_OFFLINE is set.
"""
from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path

from ..clip.detector import hub_cache
from ..nli.classifier import offline

NAME = "mlx"
DEVICE = "Apple GPU (MLX)"
INSTALL_HINT = 'pip install "social-video-editor[apple]"'
MODEL_REPOS = {size: f"mlx-community/whisper-{size}-mlx"
               for size in ("tiny", "base", "small", "medium", "large-v2", "large-v3")}
MODEL_REPOS["large-v3-turbo"] = "mlx-community/whisper-large-v3-turbo"
WEIGHTS = ("weights.safetensors", "weights.npz", "model.safetensors")


def model_repo(model: str | None) -> str:
    """transcription.model (tiny … large-v3, the same names as faster-whisper) → MLX model repo or folder."""
    model = str(model or "small")
    return MODEL_REPOS.get(model, model)


def apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def downloaded(model: str | None) -> bool:
    """Cheap check (nothing imported): the model is a local folder or its weights are in the HF cache."""
    repo = model_repo(model)
    if Path(repo).expanduser().is_dir():
        return True
    snapshots = hub_cache() / ("models--" + repo.replace("/", "--")) / "snapshots"
    return snapshots.is_dir() and any((s / w).exists() for s in snapshots.iterdir() for w in WEIGHTS)


class MlxWhisperTranscriber:
    name = NAME
    device = DEVICE

    def ready(self, settings: dict | None = None) -> str | None:
        """None when this engine can run here; else why not (shown by `sve doctor`, logged on fallback)."""
        if not apple_silicon():
            return "needs a Mac with Apple Silicon"
        if importlib.util.find_spec("mlx_whisper") is None or importlib.util.find_spec("mlx") is None:
            return f"not installed ({INSTALL_HINT})"
        model = (settings or {}).get("model", "small")
        if offline() and not downloaded(model):
            return f"the {model} model ({model_repo(model)}) is not downloaded and downloads are off (SVE_OFFLINE)"
        return None

    def run(self, wav: str, settings: dict, workdir: Path) -> list[dict] | None:
        """Words, or None when the engine failed (the caller falls back to another one)."""
        from ..whisper.transcriber import run_worker

        return run_worker("social_video_editor.adapters.mlx_whisper.worker", wav, settings, workdir, NAME)

    def transcribe(self, wav: str, settings: dict, workdir: Path) -> list[dict]:
        return self.run(wav, settings, workdir) or []
