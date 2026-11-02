"""Use case: prepare every source (probe, proxy, 16 kHz wav) and analyze it (cached in the project).

Analysis of a source (cache/<id>_analysis.json): samples (brightness, sharpness, motion, color),
scenes, audio levels and peaks, Whisper words and speech segments.
"""
from __future__ import annotations

from pathlib import Path

from ..domain.models import Project
from ..domain.selection import voice_segments
from ..log import log
from .services import Services


def source_id(path: str | Path) -> str:
    """Short, safe name for cache files."""
    return "".join(c if c.isalnum() else "_" for c in Path(path).stem).strip("_")


def prepare_source(svc: Services, source: dict, cache: Path, cfg: dict) -> dict:
    """Media info + light copies (proxy video, 16 kHz wav) of one source."""
    path = source["path"]
    fid = source_id(path)
    info = svc.cache.load(cache, "media", fid) or svc.probe.probe(path)
    info["id"] = fid
    proxy = cache / f"{fid}_proxy.mp4"
    if not proxy.exists():
        svc.encoder.make_proxy(info, proxy, float(cfg["color"].get("hdr_white", 0.62)))
    info["proxy"] = str(proxy)
    wav = cache / f"{fid}_16k.wav"
    if info["has_audio"] and not wav.exists():
        svc.encoder.extract_wav(info, wav)
    info["wav"] = str(wav) if info["has_audio"] else None
    svc.cache.save(cache, "media", fid, info)
    return info


def analyze_source(svc: Services, info: dict, cache: Path, cfg: dict, force: bool = False) -> dict:
    if not force:
        cached = svc.cache.load(cache, "analysis", info["id"])
        if cached is not None:
            return cached
    a = cfg.get("analysis", {})
    log(f"Analyzing {Path(info['path']).name}: image…")
    data = {"id": info["id"], "duration": info["duration"],
            "samples": svc.video.samples(info["proxy"], float(a.get("samples_per_sec", 4)))}
    log("  scenes…")
    data["scenes"] = svc.video.scenes(info["proxy"], float(a.get("scene_threshold", 27)))
    if info.get("wav"):
        log("  audio…")
        data["audio"] = svc.audio.levels(info["wav"])
        log("  transcription (Whisper)…")
        data["words"] = svc.transcriber.transcribe(info["wav"], cfg.get("transcription", {}), cache)
    else:
        data["audio"], data["words"] = None, []
    segs = voice_segments(data["words"])
    data["voice"] = {"segments": segs,
                     "ratio": round(sum(b - a_ for a_, b in segs) / max(info["duration"], 1e-3), 3)}
    svc.cache.save(cache, "analysis", info["id"], data)
    log(f"  done: {len(data['scenes'])} scenes, {len(data['words'])} words, "
        f"speech in {data['voice']['ratio'] * 100:.0f}% of the video")
    return data


def prepare_sources(svc: Services, project: Project, cfg: dict | None = None,
                    force: bool = False) -> tuple[list[dict], list[dict]]:
    """→ (media infos, analyses) of every source, in story order."""
    cfg = cfg or project.cfg
    infos = [prepare_source(svc, s, project.cache, cfg) for s in project.sources]
    return infos, [analyze_source(svc, i, project.cache, cfg, force) for i in infos]
