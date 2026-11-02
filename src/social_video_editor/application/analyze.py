"""Use case: prepare every source (probe, proxy, 16 kHz wav) and analyze it (cached in the project).

Analysis of a source (cache/<id>_analysis.json): samples (brightness, sharpness, motion, color),
scenes, audio levels and peaks, Whisper words and speech segments.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..domain.models import Project
from ..domain.selection import voice_segments
from ..log import log
from .services import Services


def _stem(path: str | Path) -> str:
    return "".join(c if c.isalnum() else "_" for c in Path(path).stem).strip("_") or "source"


def source_id(fingerprint: dict) -> str:
    """Short, safe and unique name for cache files: <stem>_<hash of resolved path, size, mtime>.

    Two sources with the same file name in different folders get different ids, and a file that is
    replaced (new size or modification time) gets a new id, so it is analyzed again.
    """
    key = f"{fingerprint['path']}|{fingerprint.get('size')}|{fingerprint.get('mtime_ns')}"
    return f"{_stem(fingerprint['path'])}_{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def _same_file(info: dict | None, fp: dict, stored_fp: dict | None) -> bool:
    """Whether a cached media info describes the file `fp` (same resolved path; same size if recorded)."""
    if not isinstance(info, dict) or stored_fp is None or stored_fp["path"] != fp["path"]:
        return False
    return info.get("size") is None or info.get("size") == fp.get("size")


def resolve_source_id(svc: Services, cache: Path, path: str | Path) -> str:
    """Cache id of a source. Caches written by earlier versions used the bare file stem
    (<stem>_media.json / <stem>_info.json); they are still used when they describe this very file."""
    fp = svc.probe.fingerprint(path)
    legacy = _stem(path)
    info = svc.cache.load(cache, "media", legacy)
    if isinstance(info, dict) and info.get("path") and _same_file(info, fp, svc.probe.fingerprint(info["path"])):
        return legacy
    return source_id(fp)


def prepare_source(svc: Services, source: dict, cache: Path, cfg: dict) -> dict:
    """Media info + light copies (proxy video, 16 kHz wav) of one source."""
    path = source["path"]
    fid = resolve_source_id(svc, cache, path)
    info = svc.cache.load(cache, "media", fid) or svc.probe.probe(path)
    if info.get("size") is None:                  # recorded so a replaced file is noticed (see resolve_source_id)
        info["size"] = svc.probe.fingerprint(path)["size"]
    info["id"] = fid
    proxy = cache / f"{fid}_proxy.mp4"
    if not svc.cache.has(proxy):
        svc.encoder.make_proxy(info, proxy, float(cfg["color"].get("hdr_white", 0.62)))
    info["proxy"] = str(proxy)
    wav = cache / f"{fid}_16k.wav"
    if info["has_audio"] and not svc.cache.has(wav):
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
