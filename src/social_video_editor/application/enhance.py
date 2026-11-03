"""Use case: `sve enhance <project>` — better-looking photos for a slideshow (originals never modified).

For every photo source (EXIF orientation applied):
  - short side below the output's (e.g. 640×480 for a 1080p video): enlarged ×4 by the upscaler
    (Real-ESRGAN when available, else Lanczos + unsharp mask), then reduced with Lanczos so its short
    side equals the output short side;
  - short side above enhance.headroom (1.25) × the output's: only reduced to that (room for Ken Burns,
    much faster rendering);
  - in between: kept as it is.
Results go to <project>/cache/photos/<hash of path + modification time>.jpg (quality 95). Photos already
done are skipped; the render uses the enhanced copy automatically when it exists.
"""
from __future__ import annotations

import time
from pathlib import Path

from ..domain.models import Project, UserError
from ..domain.photos import ENHANCE_HEADROOM, enhance_target, enhanced_name
from ..log import log
from .analyze import is_photo, prepare_sources
from .services import Services

PHOTO_CACHE = "photos"


def enhance_settings(cfg: dict) -> dict:
    return dict(cfg.get("enhance") or {})


def enhanced_path(svc: Services, cache: Path, photo: str | Path) -> Path:
    """Where the enhanced copy of `photo` is (or would be) cached."""
    fp = svc.probe.fingerprint(photo)
    return Path(cache) / PHOTO_CACHE / enhanced_name(fp["path"], fp["mtime_ns"])


def enhanced_or_none(svc: Services, cache: Path, photo: str | Path) -> Path | None:
    """The enhanced copy of `photo` if `sve enhance` made one, else None (the render uses the original)."""
    p = enhanced_path(svc, cache, photo)
    return p if svc.cache.has(p) else None


def output_short_side(cfg: dict) -> int:
    return min(int(cfg["output"]["width"]), int(cfg["output"]["height"]))


def _short_side(svc: Services, path: Path) -> int | None:
    try:
        info = svc.probe.probe(path)
        return min(int(info["width"]), int(info["height"]))
    except (OSError, KeyError, ValueError):
        return None


def enhance_photos(svc: Services, project: Project, force: bool = False) -> dict:
    """Enhances every photo source; returns counts {"upscaled", "downscaled", "kept", "skipped", "methods"}."""
    if svc.upscaler is None:
        raise UserError("No image upscaler is configured")
    cfg = project.cfg
    settings = enhance_settings(cfg)
    headroom = float(settings.get("headroom", ENHANCE_HEADROOM))
    out_short = output_short_side(cfg)
    infos, _ = prepare_sources(svc, project, cfg)
    photos = [i for i in infos if is_photo(i)]
    counts = {"upscaled": 0, "downscaled": 0, "kept": 0, "skipped": 0, "methods": {}}
    if not photos:
        log("No photo sources to enhance.")
        return counts
    log(f"Enhancing {len(photos)} photos for a {out_short}-px short side: {svc.upscaler.describe(settings)}")
    t0 = time.time()
    for k, info in enumerate(photos, start=1):
        name = Path(info["path"]).name
        action, short = enhance_target(info["width"], info["height"], out_short, headroom)
        if action == "keep":
            counts["kept"] += 1
            continue
        out = enhanced_path(svc, project.cache, info["path"])
        if not force and svc.cache.has(out) and _short_side(svc, out) == short:
            counts["skipped"] += 1
            continue
        t = time.time()
        method = svc.upscaler.enhance(Path(info["path"]), out, short, action == "upscale", settings)
        counts["upscaled" if action == "upscale" else "downscaled"] += 1
        counts["methods"][method] = counts["methods"].get(method, 0) + 1
        log(f"  [{k}/{len(photos)}] {name} {info['width']}×{info['height']} → short side {short} "
            f"({method}, {time.time() - t:.1f} s)")
    log(f"Done in {time.time() - t0:.0f} s: {counts['upscaled']} enlarged, {counts['downscaled']} reduced, "
        f"{counts['kept']} kept as they are, {counts['skipped']} already done → {project.cache / PHOTO_CACHE}")
    return counts
