"""ImageUpscaler with Real-ESRGAN (RealESRGAN_x4plus) loaded by spandrel and run with torch.

Optional: `pip install -e ".[enhance]"`. The model file is looked up at `enhance.model`, else
$XDG_CACHE_HOME/social-video-editor/models/RealESRGAN_x4plus.pth, else
~/.cache/social-video-editor/models/RealESRGAN_x4plus.pth (it is never downloaded automatically).
Device: `enhance.device` (auto = Apple GPU "mps" when available, else CPU; mps errors fall back to CPU).
The photo is processed in overlapping tiles (`enhance.tile`, default 384 px) so memory stays bounded.
When spandrel, torch or the model are missing, the fallback (Lanczos) is used. No face restoration.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ...log import log
from .lanczos import LanczosUpscaler, load_oriented, resize_short, save_jpeg

MODEL_FILE = "RealESRGAN_x4plus.pth"
TILE = 384
OVERLAP = 16


def model_candidates(settings: dict) -> list[Path]:
    if settings.get("model"):
        return [Path(str(settings["model"])).expanduser()]
    out = []
    if os.environ.get("XDG_CACHE_HOME"):
        out.append(Path(os.environ["XDG_CACHE_HOME"]) / "social-video-editor" / "models" / MODEL_FILE)
    out.append(Path.home() / ".cache" / "social-video-editor" / "models" / MODEL_FILE)
    return out


def find_model(settings: dict) -> Path | None:
    return next((p for p in model_candidates(settings) if p.is_file()), None)


def tiles(length: int, tile: int, overlap: int) -> list[tuple[int, int, int, int]]:
    """Along one axis: (read_start, read_end, keep_start, keep_end) in input pixels. Tiles are read with
    `overlap` extra pixels on each side and only their inner part is kept, so seams never show."""
    out, start = [], 0
    while start < length:
        end = min(start + tile, length)
        out.append((max(start - overlap, 0), min(end + overlap, length), start, end))
        start = end
    return out


def upscale_tiled(model, rgb: np.ndarray, scale: int, device: str, tile: int = TILE,
                  overlap: int = OVERLAP) -> np.ndarray:
    """uint8 RGB H×W×3 → uint8 RGB (H·scale)×(W·scale)×3, one tile at a time."""
    import torch

    h, w = rgb.shape[:2]
    out = np.zeros((h * scale, w * scale, 3), np.uint8)
    with torch.inference_mode():
        for y0, y1, ky0, ky1 in tiles(h, tile, overlap):
            for x0, x1, kx0, kx1 in tiles(w, tile, overlap):
                patch = torch.from_numpy(np.array(rgb[y0:y1, x0:x1], copy=True)).to(device)
                patch = patch.permute(2, 0, 1).unsqueeze(0).float().div_(255.0)
                res = model(patch)[0].clamp_(0, 1).mul_(255.0).round_().byte().permute(1, 2, 0).cpu().numpy()
                out[ky0 * scale:ky1 * scale, kx0 * scale:kx1 * scale] = \
                    res[(ky0 - y0) * scale:(ky1 - y0) * scale, (kx0 - x0) * scale:(kx1 - x0) * scale]
    return out


class RealEsrganUpscaler:
    def __init__(self, fallback=None):
        self.fallback = fallback or LanczosUpscaler()
        self._models: dict[tuple[str, str], object] = {}
        self._warned = False

    # ── availability ─────────────────────────────────────────────────────────
    def ready(self, settings: dict) -> str | None:
        """None when Real-ESRGAN can be used; otherwise why not."""
        try:
            import spandrel  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return 'spandrel/torch are not installed (pip install -e ".[enhance]")'
        if find_model(settings) is None:
            return f"model not found: {model_candidates(settings)[-1]}"
        return None

    def device(self, settings: dict) -> str:
        import torch

        d = str(settings.get("device") or "auto")
        if d == "auto":
            return "mps" if torch.backends.mps.is_available() else "cpu"
        if d == "mps" and not torch.backends.mps.is_available():
            return "cpu"
        return d

    def describe(self, settings: dict) -> str:
        why = self.ready(settings)
        if why:
            return f"{self.fallback.describe(settings)} — Real-ESRGAN not used: {why}"
        return f"Real-ESRGAN x4 ({find_model(settings).name}) on {self.device(settings)}"

    def _model(self, settings: dict, device: str):
        from spandrel import ImageModelDescriptor, ModelLoader

        path = find_model(settings)
        key = (str(path), device)
        if key not in self._models:
            model = ModelLoader().load_from_file(path)
            if not isinstance(model, ImageModelDescriptor):
                raise ValueError(f"{path.name} is not an image-to-image model")
            self._models[key] = model.to(device).eval()
        return self._models[key]

    # ── use ──────────────────────────────────────────────────────────────────
    def upscale_array(self, rgb: np.ndarray, settings: dict) -> np.ndarray:
        """uint8 RGB → ×scale (4 for RealESRGAN_x4plus), on the configured device; mps errors → CPU."""
        device = self.device(settings)
        tile = int(settings.get("tile", TILE))
        try:
            model = self._model(settings, device)
            return upscale_tiled(model, rgb, int(model.scale), device, tile)
        except RuntimeError as e:
            if device == "cpu":
                raise
            log(f"Warning: Real-ESRGAN failed on {device} ({str(e)[:120]}); using the CPU.")
            model = self._model(settings, "cpu")
            return upscale_tiled(model, rgb, int(model.scale), "cpu", tile)

    def enhance(self, src: Path, out: Path, short_side: int, upscale: bool, settings: dict) -> str:
        from PIL import Image

        why = self.ready(settings) if upscale else None
        if upscale and why:
            if not self._warned:
                log(f"Real-ESRGAN not used ({why}); enlarging with Lanczos instead.")
                self._warned = True
            return self.fallback.enhance(src, out, short_side, upscale, settings)
        img = load_oriented(src)
        method = "lanczos"
        if upscale:
            img = Image.fromarray(self.upscale_array(np.asarray(img, np.uint8), settings))
            method = "realesrgan"
        save_jpeg(resize_short(img, int(short_side)), out)
        return method
