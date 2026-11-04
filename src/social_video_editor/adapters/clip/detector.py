"""VisualDetector with CLIP through sentence-transformers (runs locally, CPU by default).

Images are embedded with clip-ViT-B-32; texts with a text model that writes into the SAME space
(clip-ViT-B-32-multilingual-v1 understands Spanish and 50+ languages; the CLIP model itself only
English). The models are downloaded once (≈1.2 GB in total) to the Hugging Face cache.

Frames come from the low-resolution proxy of each source, sampled `fps` times per second; their
embeddings are cached in the project cache (<id>_clip.npy + <id>_clip.json with the timestamps),
so a source is only embedded once. Text embeddings are cached in memory.

sentence-transformers is imported lazily: without it the engine still works (no vision).
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import numpy as np

from ...log import log
from ..files.atomic import publish, temp_name
from ..opencv.capture import open_capture

DEFAULT_MODEL = "sentence-transformers/clip-ViT-B-32"
DEFAULT_TEXT_MODEL = "sentence-transformers/clip-ViT-B-32-multilingual-v1"
CACHE_VERSION = 1
INSTALL_HINT = "install the optional extra: pip install 'social-video-editor[vision]'"
DOWNLOAD_HINT = ("the CLIP models are not downloaded yet (≈1.2 GB, only once); "
                 "`sve products <project>` downloads them, or set vision.enabled: true")


def hub_cache() -> Path:
    """Folder of the Hugging Face model cache (same rules as huggingface_hub)."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    home = os.environ.get("HF_HOME") or os.path.join(os.environ.get("XDG_CACHE_HOME") or "~/.cache", "huggingface")
    return Path(home).expanduser() / "hub"


def _unit(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


class ClipVisualDetector:
    def __init__(self, cache_folder: str | None = None):
        self.cache_folder = cache_folder         # None = the default Hugging Face cache
        self._models: dict[tuple[str, str], object] = {}
        self._texts: dict[tuple[str, str], np.ndarray] = {}

    # ── models ────────────────────────────────────────────────────────────────
    @staticmethod
    def _names(settings: dict) -> tuple[str, str]:
        return (settings.get("model") or DEFAULT_MODEL, settings.get("text_model") or DEFAULT_TEXT_MODEL)

    def model_name(self, settings: dict) -> str:
        return self._names(settings)[0].rstrip("/").split("/")[-1]

    @staticmethod
    def _device(settings: dict) -> str:
        """vision.device: cpu (default; does not import torch), auto (cuda, else mps, else cpu), cuda or
        mps; a GPU that is not available → cpu (warned once)."""
        device = str(settings.get("device") or "cpu").strip().lower()
        if device == "cpu":
            return device
        from ..upscale.realesrgan import pick_device
        return pick_device(device, "vision.device")

    def _model(self, name: str, settings: dict, download: bool):
        device = self._device(settings)
        key = (name, device)
        if key not in self._models:
            from sentence_transformers import SentenceTransformer
            try:
                from transformers.utils import logging as hf_logging
                hf_logging.disable_progress_bar()
            except Exception:  # noqa: BLE001 - cosmetic only
                pass
            self._models[key] = SentenceTransformer(name, device=device, cache_folder=self.cache_folder,
                                                    local_files_only=not download)
        return self._models[key]

    def _downloaded(self, name: str) -> bool:
        """Cheap check (no torch import): the model is a local folder or is in the model cache."""
        if Path(name).expanduser().is_dir():
            return True
        folder = Path(self.cache_folder) if self.cache_folder else hub_cache()
        return (folder / ("models--" + name.replace("/", "--")) / "snapshots").is_dir()

    def ready(self, settings: dict, download: bool = False) -> str | None:
        """None when the models can be used; otherwise the reason (for a friendly message)."""
        if importlib.util.find_spec("sentence_transformers") is None:
            return f"sentence-transformers is not installed; {INSTALL_HINT}"
        image_model, text_model = self._names(settings)
        if not download and not all(self._downloaded(n) for n in (image_model, text_model)):
            return DOWNLOAD_HINT
        try:
            if download:
                log("Loading the CLIP models (the first time they are downloaded, ≈1.2 GB)…")
            self._model(image_model, settings, download)
            self._model(text_model, settings, download)
        except OSError as e:
            return DOWNLOAD_HINT if not download else f"the CLIP models could not be loaded ({e})"
        return None

    def _encode(self, model_name: str, items: list, settings: dict) -> np.ndarray:
        model = self._model(model_name, settings, download=False)
        out = model.encode(items, batch_size=int(settings.get("batch_size", 32)), convert_to_numpy=True,
                           normalize_embeddings=True, show_progress_bar=False)
        if not np.isfinite(out).all() and self._device(settings) != "cpu":      # e.g. NaNs on MPS
            log("Warning: the vision model produced invalid values on the GPU; using the CPU.")
            settings["device"] = "cpu"
            return self._encode(model_name, items, settings)
        return _unit(out)

    # ── texts ─────────────────────────────────────────────────────────────────
    def text_embeddings(self, texts: list[str], settings: dict) -> np.ndarray:
        """Unit embeddings [len(texts), d] (cached in memory)."""
        name = self._names(settings)[1]
        missing = list(dict.fromkeys(t for t in texts if (name, t) not in self._texts))
        if missing:
            for t, e in zip(missing, self._encode(name, missing, settings)):
                self._texts[(name, t)] = e
        if not texts:
            return np.zeros((0, 512), np.float32)
        return np.stack([self._texts[(name, t)] for t in texts])

    # ── frames ────────────────────────────────────────────────────────────────
    def frame_embeddings(self, video: str, cache_dir: Path, key: str, settings: dict) -> tuple[np.ndarray, np.ndarray]:
        """(times [n], unit embeddings [n, d]) of the frames of `video` sampled `fps` times per second."""
        fps = float(settings.get("fps", 2))
        model = self.model_name(settings)
        npy, meta_p = Path(cache_dir) / f"{key}_clip.npy", Path(cache_dir) / f"{key}_clip.json"
        if npy.exists() and meta_p.exists() and npy.stat().st_mtime >= Path(video).stat().st_mtime:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            if meta.get("model") == model and meta.get("fps") == fps and meta.get("v") == CACHE_VERSION:
                emb = np.load(npy)
                if len(emb) == len(meta["times"]):
                    return np.asarray(meta["times"], float), emb.astype(np.float32)
        times, emb = self._embed_video(video, fps, settings)
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        tmp = temp_name(npy)          # unique: two processes may embed the same video at the same time
        try:
            np.save(tmp, emb.astype(np.float16))
            publish(tmp, npy)
        finally:
            tmp.unlink(missing_ok=True)
        meta_p.write_text(json.dumps({"model": model, "fps": fps, "v": CACHE_VERSION,
                                      "times": [round(float(t), 3) for t in times]}), encoding="utf-8")
        return times, emb

    def _embed_video(self, video: str, fps: float, settings: dict) -> tuple[np.ndarray, np.ndarray]:
        import cv2
        from PIL import Image

        name = self._names(settings)[0]
        cap = open_capture(video)
        vfps = cap.get(cv2.CAP_PROP_FPS) or 30
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(int(round(vfps / fps)), 1)
        log(f"Vision: embedding {Path(video).name} ({total // step if total else '?'} frames)…")
        times, chunks, batch, i = [], [], [], 0
        while cap.grab():
            if i % step == 0:
                ok, f = cap.retrieve()
                if ok:
                    batch.append(Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
                    times.append(i / vfps)
                if len(batch) >= 128:
                    chunks.append(self._encode(name, batch, settings))
                    batch = []
            i += 1
        cap.release()
        if batch:
            chunks.append(self._encode(name, batch, settings))
        emb = np.concatenate(chunks) if chunks else np.zeros((0, 512), np.float32)
        return np.asarray(times, float), emb
