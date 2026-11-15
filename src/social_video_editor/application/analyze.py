"""Use case: prepare every source (probe, proxy, 16 kHz wav) and analyze it (cached in the project).

Analysis of a source (cache/<id>_analysis.json): samples (brightness, sharpness, motion, color),
scenes, audio levels and peaks, Whisper words and speech segments.

Sources are prepared and analyzed side by side (analysis.jobs; auto = render.jobs / $SVE_JOBS): every
source's result and cache files are the same as one at a time, only sooner. Whisper still runs one
source at a time (each transcription is multi-threaded and holds its model in memory).

Still photos are sources too: a source may be a photo or a FOLDER (scanned recursively; each photo in
it becomes one source, sorted by path). Photos only need their media info (size, EXIF orientation,
capture time): no proxy, no audio, no Whisper, no scene detection.
"""
from __future__ import annotations

import copy
import hashlib
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..domain.config import resolve_jobs
from ..domain.models import Project
from ..domain.selection import voice_segments
from ..log import log
from .services import Services

DEFAULT_ENGINE = "faster-whisper"      # the engine of caches written before transcription.engine existed
_WHISPER = threading.Lock()            # one transcription at a time, whatever the number of jobs


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


def expand_sources(svc: Services, sources: list[dict]) -> list[dict]:
    """Sources with every folder replaced by the photos inside it (recursively, sorted by path)."""
    out = []
    for s in sources:
        path = Path(str(s["path"])).expanduser()
        if svc.photos is not None and path.is_dir():
            found = svc.photos.scan(path)
            if not found:
                log(f"Warning: no photos found in {path}")
            out += [{**s, "path": p, "folder": str(path)} for p in found]
        else:
            out.append(s)
    return out


def is_photo(info: dict) -> bool:
    return bool(info.get("image"))


def prepare_source(svc: Services, source: dict, cache: Path, cfg: dict) -> dict:
    """Media info + light copies (proxy video, 16 kHz wav) of one source (only media info for a photo)."""
    path = source["path"]
    fid = resolve_source_id(svc, cache, path)
    info = svc.cache.load(cache, "media", fid) or svc.probe.probe(path)
    if info.get("size") is None:                  # recorded so a replaced file is noticed (see resolve_source_id)
        info["size"] = svc.probe.fingerprint(path)["size"]
    info["id"] = fid
    if is_photo(info):
        if info.get("proxy", "") is not None or info.get("wav", "") is not None:
            info["proxy"] = info["wav"] = None
            svc.cache.save(cache, "media", fid, info)
        return info
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


def photo_analysis(info: dict) -> dict:
    """What the planner and renderer read from a photo's "analysis" (nothing to analyze; not cached)."""
    return {"id": info["id"], "duration": 0.0, "image": True, "samples": [], "scenes": [], "audio": None,
            "words": [], "voice": {"segments": [], "ratio": 0.0}}


def transcription_settings(cfg: dict, previous: dict | None = None) -> dict:
    """The transcription settings for one source. With `engine: auto`, a source that was analyzed
    before keeps the engine that transcribed it (an analysis cached before engines were recorded =
    faster-whisper): re-analyzing a finished project never swaps the engine its words came from."""
    t = dict(cfg.get("transcription") or {})
    if isinstance(previous, dict) and str(t.get("engine") or "").strip().lower() == "auto":
        t["engine"] = (previous.get("transcription") or {}).get("engine") or DEFAULT_ENGINE
    return t


def transcribe(svc: Services, wav: str, settings: dict, cache: Path) -> tuple[list[dict], dict | None]:
    """(words, {"engine", "model", "device"} or None when the transcriber does not say)."""
    with _WHISPER:
        with_info = getattr(svc.transcriber, "transcribe_info", None)
        if with_info is not None:
            return with_info(wav, settings, cache)
        return svc.transcriber.transcribe(wav, settings, cache), None


def analyze_source(svc: Services, info: dict, cache: Path, cfg: dict, force: bool = False) -> dict:
    if is_photo(info):
        return photo_analysis(info)
    cached = svc.cache.load(cache, "analysis", info["id"])
    if not force and cached is not None:
        if "face" not in cached and _add_face(svc, info, cached, cfg):      # analyzed before framing.face was on
            svc.cache.save(cache, "analysis", info["id"], cached)
        return cached
    a = cfg.get("analysis", {})
    name = Path(info["path"]).name
    log(f"Analyzing {name}: image…")
    data = {"id": info["id"], "duration": info["duration"],
            "samples": svc.video.samples(info["proxy"], float(a.get("samples_per_sec", 4)))}
    log(f"  scenes ({name})…")
    data["scenes"] = svc.video.scenes(info["proxy"], float(a.get("scene_threshold", 27)))
    if info.get("wav"):
        log(f"  audio ({name})…")
        data["audio"] = svc.audio.levels(info["wav"])
        log(f"  transcription ({name})…")
        data["words"], used = transcribe(svc, info["wav"], transcription_settings(cfg, cached), cache)
        if used:
            data["transcription"] = used
    else:
        data["audio"], data["words"] = None, []
    segs = voice_segments(data["words"])
    data["voice"] = {"segments": segs,
                     "ratio": round(sum(b - a_ for a_, b in segs) / max(info["duration"], 1e-3), 3)}
    _add_face(svc, info, data, cfg)
    svc.cache.save(cache, "analysis", info["id"], data)
    used = data.get("transcription")
    log(f"  done ({name}): {len(data['scenes'])} scenes, {len(data['words'])} words"
        + (f" ({used['engine']}, {used['model']})" if used else "")
        + f", speech in {data['voice']['ratio'] * 100:.0f}% of the video")
    return data


def _add_face(svc: Services, info: dict, data: dict, cfg: dict) -> bool:
    """framing.face (false | auto | true): finds the presenter's face in the proxy and stores it as
    data["face"] (see VideoAnalyzer.face). auto uses a detector only when one is already available;
    true may download a small model once. Returns whether the analysis changed."""
    mode = (cfg.get("framing") or {}).get("face", False)
    if mode in (False, None, "false") or not info.get("proxy"):
        return False
    log(f"  face ({Path(info['path']).name})…")
    face = svc.video.face(info["proxy"], download=mode is True or mode == "true")
    if face is None:
        return False
    data["face"] = face
    return True


def analysis_jobs(svc: Services, cfg: dict) -> int:
    """analysis.jobs: sources prepared and analyzed at once. auto = as many as render.jobs ($SVE_JOBS);
    1 = one after another, as before."""
    setting = (cfg.get("analysis") or {}).get("jobs", "auto")
    if setting is None or (isinstance(setting, str) and setting.strip().lower() == "auto"):
        jobs = getattr(svc.encoder, "parallel_jobs", None)
        return jobs(cfg) if jobs is not None else 1
    return resolve_jobs(setting, None, 1)


def in_parallel(fn: Callable, items: list, jobs: int, key: Callable | None = None) -> list:
    """[fn(x) for x in items] with up to `jobs` at once; same order, the first error is raised.

    Items with the same `key` (one file listed twice) are worked on once — two jobs never write the
    same cache file — and each later one gets its own copy of the result, as a cached load would."""
    if jobs <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    keys = [key(x) if key else k for k, x in enumerate(items)]
    first = {}
    for k, x in zip(keys, items):
        first.setdefault(k, x)
    with ThreadPoolExecutor(max_workers=min(jobs, len(first)), thread_name_prefix="sve-analyze") as pool:
        done = dict(zip(first, pool.map(fn, first.values())))
    out, seen = [], set()
    for k in keys:
        out.append(done[k] if k not in seen else copy.deepcopy(done[k]))
        seen.add(k)
    return out


def prepare_sources(svc: Services, project: Project, cfg: dict | None = None,
                    force: bool = False) -> tuple[list[dict], list[dict]]:
    """→ (media infos, analyses) of every source, in story order."""
    cfg = cfg or project.cfg
    jobs = analysis_jobs(svc, cfg)
    infos = in_parallel(lambda s: prepare_source(svc, s, project.cache, cfg), expand_sources(svc, project.sources), jobs,
                        key=lambda s: str(Path(str(s["path"])).expanduser()))
    photos = sum(is_photo(i) for i in infos)
    if photos:
        log(f"Sources: {len(infos) - photos} videos, {photos} photos")
    return infos, in_parallel(lambda i: analyze_source(svc, i, project.cache, cfg, force), infos, jobs,
                              key=lambda i: i["id"])
