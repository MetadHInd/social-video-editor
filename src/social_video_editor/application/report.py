"""Use case: a compact project report (one screen of text, or JSON) for people and AI agents.

Reads only what is already on disk — project.yaml, plan.json, the cache (media info, music
analysis, products.json) and the version folders (summary.json, check.json). It never probes,
analyzes or renders anything, so it is instant and cheap to read:

  sve report <project>          # ≲50 lines
  sve report <project> --json   # the same data as JSON
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from pathlib import Path

from ..domain.models import Project, UserError
from ..domain.pieces import Group, Sequence
from ..domain.timebase import mmss
from ..domain.timeline import compute_timeline
from .analyze import source_id
from .services import Services

LOW_CONFIDENCE = 0.5      # products below this confidence: look at the images before trusting them
MAX_SHOT_LINES = 10       # condensed shot lines (several shots per line)
MAX_PRODUCTS = 8
MAX_MESSAGES = 3          # errors / warnings shown per kind
WIDTH = 112               # max characters of a condensed line
MEDIA_EXT = (".mp4", ".mov", ".srt", ".jpg", ".png", ".wav")
MUSIC_SETTINGS = ("bpm", "tempo_factor", "drop_at", "align")   # optional music settings worth showing


# ── data ─────────────────────────────────────────────────────────────────────

def build_report(svc: Services, project: Project) -> dict:
    cfg = project.cfg
    versions = _versions(svc, project)
    latest = versions["latest"]
    return {
        "project": project.name,
        "recipe": project.recipe,
        "language": project.language,
        "sources": _sources(svc, project),
        "plan": _plan(svc, project),
        "music": _music(svc, project, cfg, latest),
        "versions": versions,
        "products": _products(svc, project),
        "check": _check(svc, project, latest),
    }


def _sources(svc: Services, project: Project) -> list[dict]:
    out = []
    for i, s in enumerate(project.sources):
        info = svc.cache.load(project.cache, "media", source_id(s["path"])) or {}
        out.append({"index": i, "file": Path(s["path"]).name, "duration": info.get("duration"),
                    "hdr": info.get("hdr"), "has_audio": info.get("has_audio")})
    return out


def _timeline_plan(plan: dict) -> dict:
    """Copy of the plan where "search" shots get provisional times (the render resolves them)."""
    p = copy.deepcopy(plan)
    for s in p.get("shots", []):
        b = s.get("search")
        if b and ("start" not in s or "end" not in s):
            a = float(b["range"][0])
            s["start"], s["end"] = a, a + float(b.get("dur", 1.8))
    return p


def _shot_entry(clip) -> dict:
    d, piece = clip.data, clip.piece
    e = {"n": clip.index, "type": piece.kind, "role": d.get("role") or None,
         "transition": piece.transition, "dur": round(clip.dur, 2), "at": round(clip.out_start, 2)}
    if piece.kind == "video":
        e.update(source=piece.source, start=d.get("start"), end=d.get("end"), speed=d.get("speed", 1))
        if d.get("search"):
            e["search"] = d["search"].get("range")
    else:
        e["from"] = sorted(piece.dependencies())
    if d.get("effects"):
        e["effects"] = [x.get("type") for x in d["effects"]]
    if d.get("group"):
        e["group"] = d["group"]
    if d.get("beats"):
        e["beats"] = d["beats"]
    return e


def _plan(svc: Services, project: Project) -> dict | None:
    plan = svc.projects.load_plan(project)
    if plan is None:
        return None
    texts = plan.get("texts", [])
    out = {"shots": len(plan.get("shots", [])), "texts": len(texts),
           "text_styles": dict(Counter(t.get("style", "simple") for t in texts).most_common()),
           "first_text": texts[0].get("text") if texts else None}
    try:
        tl = compute_timeline(Sequence.from_plan(_timeline_plan(plan)), project.cfg)
        out["duration"] = round(tl.total, 2)
        out["groups"] = sum(1 for c in tl.sequence.children() if isinstance(c, Group))
        out["timeline"] = [_shot_entry(c) for c in tl]
    except (UserError, KeyError, TypeError, ValueError) as exc:     # an incomplete plan still gets a report
        out["timeline_error"] = str(exc) or type(exc).__name__
    return out


def _music_key(path: Path) -> str:
    return path.stem.replace(" ", "_")          # same key as application.music.music_analysis


def _music(svc: Services, project: Project, cfg: dict, latest: dict | None) -> dict:
    m = cfg.get("music", {})
    out = {"file": None, "start_at": m.get("start_at", 0), "bpm": None,
           "settings": {k: m[k] for k in MUSIC_SETTINGS if m.get(k) is not None}}
    try:
        path = svc.projects.find_music(m, project.dir)
    except UserError as exc:
        out["error"] = str(exc)
        path = None
    if path is not None:
        out["file"] = path.name
        am = svc.cache.load(project.cache, "music", _music_key(path)) or {}
        out["bpm"] = am.get("tempo")
        out["drop"] = am.get("drop")
    rendered = ((latest or {}).get("summary") or {}).get("music")
    if rendered:
        out["rendered_file"] = rendered.get("file")
        out["rendered_start"] = rendered.get("start_sec")
    return out


def _version_entry(svc: Services, folder: Path) -> dict:
    summary = svc.projects.load_summary(folder) or {}
    files = sorted(p.name for p in folder.iterdir() if p.is_file() and not p.name.startswith(("_", "."))
                   and p.suffix.lower() in MEDIA_EXT)
    listen = next((d for d in (folder / "listen", folder / "escuchar") if d.is_dir()), None)
    audio = summary.get("audio") or {}
    return {
        "name": folder.name,
        "video": summary.get("video") or next((f for f in files if f.endswith(".mp4")), None),
        "duration": summary.get("duration_sec"), "shots": summary.get("shots"),
        "resolution": summary.get("resolution"), "lufs": audio.get("after_lufs"),
        "true_peak": audio.get("true_peak_dbtp"), "files": files,
        "check": (folder / "check.json").is_file(),
        "listen": len([p for p in listen.iterdir() if p.is_file()]) if listen else 0,
        "summary": summary or None,
    }


def _versions(svc: Services, project: Project) -> dict:
    folders = svc.projects.versions(project)
    last = svc.projects.last_version(project)
    out = {"all": [f.name for f in folders], "latest": None, "variants": []}
    if last is None:
        return out
    n = last.name[1:]
    out["latest"] = _version_entry(svc, last)
    out["variants"] = [_version_entry(svc, f) for f in folders
                       if f != last and re.fullmatch(rf"v{n}\D.*", f.name)]
    return out


def _products(svc: Services, project: Project) -> dict | None:
    data = svc.projects.read_json(project.cache / "products.json")
    if not isinstance(data, dict):
        return None
    segs = [s for s in data.get("segments", []) if isinstance(s, dict)]
    low = [s for s in segs if float(s.get("confidence", 0) or 0) < LOW_CONFIDENCE]
    return {"count": len(segs), "model": data.get("model"), "language": data.get("language"),
            "segments": sorted(segs, key=lambda s: (s.get("source", 0), s.get("start", 0))),
            "low_confidence": len(low)}


def _check(svc: Services, project: Project, latest: dict | None) -> dict | None:
    if not latest:
        return None
    data = svc.projects.read_json(project.dir / latest["name"] / "check.json")
    if not isinstance(data, dict):
        return None
    return {"version": data.get("version", latest["name"]), "ok": data.get("ok"),
            "errors": data.get("errors") or [], "warnings": data.get("warnings") or [],
            "metrics": data.get("metrics") or {}, "listen": data.get("listen") or []}


# ── text ─────────────────────────────────────────────────────────────────────

def _num(x, fmt: str = ".1f") -> str:
    return format(x, fmt) if isinstance(x, (int, float)) else "?"


def _pack(tokens: list[str], indent: str = "  ", max_lines: int | None = None) -> list[str]:
    """Joins tokens with " | " into lines of at most WIDTH characters."""
    lines, cur, counts = [], [], []
    for t in tokens:
        if cur and len(indent) + len(" | ".join(cur + [t])) > WIDTH:
            lines.append(indent + " | ".join(cur))
            counts.append(len(cur))
            cur = []
        cur.append(t)
    if cur:
        lines.append(indent + " | ".join(cur))
        counts.append(len(cur))
    if max_lines is not None and len(lines) > max_lines:
        rest = len(tokens) - sum(counts[:max_lines])
        lines = lines[:max_lines] + [f"{indent}… +{rest} more (sve view <project>)"]
    return lines


def _shot_token(e: dict) -> str:
    parts = [str(e["n"])]
    if e.get("role") and e["role"] not in ("story", e["type"]):
        parts.append(e["role"])
    if e["type"] == "video":
        where = (f"?{_num(e['search'][0], '.0f')}-{_num(e['search'][1], '.0f')}" if e.get("search")
                 else f"{_num(e.get('start'))}-{_num(e.get('end'))}")
        parts.append(f"s{e.get('source')} {where}")
        if e.get("speed", 1) not in (1, 1.0):
            parts.append(f"x{e['speed']}")
    else:
        parts.append(f"{e['type']}<{','.join(map(str, e.get('from', [])))}")
    parts.append(f"{e['dur']:.1f}s")
    if e.get("transition") and e["transition"] != "cut":
        parts.append(f"/{e['transition']}")
    if e.get("effects"):
        parts.append(f"+{','.join(x for x in e['effects'] if x)}")
    return " ".join(parts)


def _message(m) -> str:
    if isinstance(m, dict):
        m = m.get("message") or m.get("text") or ", ".join(f"{k}={v}" for k, v in m.items())
    m = str(m)
    return m if len(m) <= WIDTH - 6 else m[:WIDTH - 7] + "…"


def _version_line(v: dict) -> str:
    bits = [f"{v['name']:<12} {v['video'] or '(no video)'}"]
    if v.get("duration") is not None:
        bits.append(f"{_num(v['duration'])} s")
    if v.get("shots") is not None:
        bits.append(f"{v['shots']} shots")
    if v.get("lufs") is not None:
        bits.append(f"{v['lufs']} LUFS")
    extras = [f for f in v["files"] if f != v["video"]]
    if extras:
        bits.append("+" + ", ".join(Path(f).suffix.lstrip(".") for f in extras))
    if v.get("check"):
        bits.append("check.json")
    if v.get("listen"):
        bits.append(f"listen/ {v['listen']} files")
    return "  " + " · ".join(bits)


def report_lines(r: dict) -> list[str]:
    out = [f"PROJECT {r['project']} · recipe {r['recipe']} · language {r['language']}"]

    src = r["sources"]
    known = [s["duration"] for s in src if s["duration"] is not None]
    head = f"SOURCES {len(src)}" + (f" · {mmss(sum(known))} total" if known else "")
    if len(known) < len(src):
        head += f" · {len(src) - len(known)} not analyzed yet (sve analyze)"
    out.append(head)
    out += _pack([f"{s['index']} {s['file']} {mmss(s['duration']) if s['duration'] is not None else '?'}"
                  + (" HDR" if s.get("hdr") else "") + ("" if s.get("has_audio") in (True, None) else " mute")
                  for s in src])

    p = r["plan"]
    if p is None:
        out.append("PLAN none yet (sve plan <project>)")
    else:
        line = f"PLAN {p['shots']} shots"
        if "duration" in p:
            line += f" · ~{p['duration']:.1f} s before beat alignment"
        if p.get("groups"):
            line += f" · {p['groups']} group" + ("s" if p["groups"] > 1 else "")
        styles = ", ".join(f"{k} {v}" for k, v in p["text_styles"].items())
        line += f" · {p['texts']} texts" + (f" ({styles})" if styles else "")
        out.append(line)
        if p.get("timeline_error"):
            out.append(f"  timeline not computable: {_message(p['timeline_error'])}")
        out += _pack([_shot_token(e) for e in p.get("timeline", [])], max_lines=MAX_SHOT_LINES)
        if p.get("first_text"):
            out.append(f"  first text: {_message(p['first_text'])}")

    m = r["music"]
    if m.get("error"):
        out.append(f"MUSIC {_message(m['error'])}")
    elif not m["file"] and not m.get("rendered_file"):
        out.append("MUSIC none")
    else:
        line = f"MUSIC {m['file'] or m.get('rendered_file')} · start_at {m['start_at']}"
        if m.get("rendered_start") is not None:
            line += f" (rendered from {_num(m['rendered_start'], '.2f')} s)"
        line += f" · {_num(m['bpm'], '.1f')} BPM" if m.get("bpm") else " · BPM unknown (not analyzed)"
        out.append(line + "".join(f" · {k} {x}" for k, x in m["settings"].items()))

    v = r["versions"]
    if not v["latest"]:
        out.append("VERSIONS none yet (sve render <project>)")
    else:
        names = v["all"]
        shown = names if len(names) <= 12 else ["…", *names[-11:]]
        out.append(f"VERSIONS {' '.join(shown)} · latest {v['latest']['name']}")
        for ver in [v["latest"], *v["variants"]]:
            out.append(_version_line(ver))

    pr = r["products"]
    if pr is None:
        out.append("PRODUCTS none (sve products <project> → cache/products.json)")
    else:
        meta = ", ".join(x for x in (pr.get("model"), pr.get("language")) if x)
        out.append(f"PRODUCTS {pr['count']} segments" + (f" ({meta})" if meta else ""))
        segs = pr["segments"]
        tokens = []
        for s in segs[:MAX_PRODUCTS]:
            c = float(s.get("confidence", 0) or 0)
            tokens.append(f"{s.get('label', '?')} s{s.get('source', 0)} {_num(s.get('start'))}-{_num(s.get('end'))}"
                          f" c{c:.2f}" + ("?" if c < LOW_CONFIDENCE else ""))
        out += _pack(tokens)
        if len(segs) > MAX_PRODUCTS:
            out.append(f"  … +{len(segs) - MAX_PRODUCTS} more (cache/products.json)")
        if pr["low_confidence"]:
            out.append(f"  ⚠ {pr['low_confidence']} low confidence (<{LOW_CONFIDENCE}, marked ?): look at those "
                       "images (sve thumbs <project> --source S --start A --end B)")

    ck = r["check"]
    if ck is None:
        out.append("CHECK not run for the latest version (sve check <project>)")
    else:
        status = "OK" if ck["ok"] else ("FAILED" if ck["ok"] is False else "?")
        line = (f"CHECK {ck['version']}: {status} · {len(ck['errors'])} errors · {len(ck['warnings'])} warnings")
        if ck["listen"]:
            line += f" · listen: {len(ck['listen'])} files"
        out.append(line)
        metrics = [f"{k}={x}" for k, x in ck["metrics"].items() if isinstance(x, (int, float, str, bool))]
        if metrics:
            out += _pack(metrics[:8])
        for kind in ("errors", "warnings"):
            for msg in ck[kind][:MAX_MESSAGES]:
                out.append(f"  {kind[:-1]}: {_message(msg)}")
            if len(ck[kind]) > MAX_MESSAGES:
                out.append(f"  … +{len(ck[kind]) - MAX_MESSAGES} more {kind}")
    return out


def report_json(r: dict) -> dict:
    """The report without the bulky raw summary of each version."""
    r = copy.deepcopy(r)
    for v in [r["versions"]["latest"], *r["versions"]["variants"]]:
        if v:
            v.pop("summary", None)
    return r
