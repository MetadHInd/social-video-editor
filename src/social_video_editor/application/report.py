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

from ..domain import automix, review
from ..domain import pip as pip_window
from ..domain.cutaways import resolve as resolve_cutaways
from ..domain.models import Project, UserError
from ..domain.photos import is_image_name
from ..domain.pieces import Group, Sequence
from ..domain.timebase import mmss
from ..domain.timeline import compute_timeline
from .analyze import expand_sources, resolve_source_id
from .services import Services

LOW_CONFIDENCE = 0.5      # products below this confidence: look at the images before trusting them
MAX_SHOT_LINES = 10       # condensed shot lines (several shots per line)
MAX_PRODUCTS = 8
MAX_MESSAGES = 3          # errors / warnings shown per kind
MAX_SENTENCES = 6         # script sentences listed per kind (missing, several takes, weak match)
WEAK_MATCH = 0.8          # a sentence found with a lower score is worth a look
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
        "review": _review(svc, project),
    }


def _review(svc: Services, project: Project) -> dict | None:
    """<project>/review.json: what the last `sve make` fixed by itself and the decisions it left."""
    data = svc.projects.read_json(project.dir / review.REVIEW_FILE)
    return data if isinstance(data, dict) else None


def _sources(svc: Services, project: Project) -> list[dict]:
    """One entry per source of project.yaml; a folder of photos is ONE entry with its photo count."""
    out = []
    for i, s in enumerate(project.sources):
        expanded = expand_sources(svc, [s])
        if any(e.get("folder") for e in expanded) or (not expanded and svc.photos is not None):
            out.append({"index": i, "file": Path(str(s["path"])).name + "/", "folder": True,
                        "photos": len(expanded), "duration": None, "hdr": None, "has_audio": None})
            continue
        info = svc.cache.load(project.cache, "media", resolve_source_id(svc, project.cache, s["path"])) or {}
        entry = {"index": i, "file": Path(s["path"]).name, "duration": info.get("duration"),
                 "hdr": info.get("hdr"), "has_audio": info.get("has_audio")}
        if info.get("image") or is_image_name(s["path"]):
            entry.update(photo=True, duration=None, has_audio=None)
        out.append(entry)
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
    elif piece.kind == "graphic":
        e["template"] = d.get("template")
    else:
        e["from"] = sorted(piece.dependencies())
    if any(not x.get("auto") for x in d.get("effects") or []):      # the planner's own slow zooms are not listed
        e["effects"] = [x.get("type") for x in d["effects"] if not x.get("auto")]
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
        photos = [c.dur for c in tl if c.piece.kind == "photo"]
        if photos:      # a slideshow has hundreds: summarized instead of listed
            out["photos"] = {"count": len(photos), "min_dur": round(min(photos), 2),
                             "max_dur": round(max(photos), 2)}
        out["timeline"] = [_shot_entry(c) for c in tl if c.piece.kind != "photo"]
        if plan.get("cutaways"):        # placed with an estimated entrance (nothing is drawn for a report)
            placed, warnings = resolve_cutaways(plan, tl, int(project.cfg["output"]["fps"]),
                                                pip=pip_window.settings(project.cfg))
            out["cutaways"] = {"count": len(plan["cutaways"]), "warnings": warnings,
                               "proposed": sum(1 for c in placed if c.auto),
                               "broll": _broll(plan),
                               "placed": [{"n": c.index, "type": c.kind, "what": c.label, "start": c.start,
                                           "end": c.end, "overlay": c.overlay, "pip": c.pip, "auto": c.auto,
                                           "sentences": list(c.sentences) if c.sentences else None} for c in placed]}
    except (UserError, KeyError, TypeError, ValueError) as exc:     # an incomplete plan still gets a report
        out["timeline_error"] = str(exc) or type(exc).__name__
    if plan.get("sentences"):
        out["script"] = _script(plan)
    return out


def _broll(plan: dict) -> dict | None:
    """The b-roll cutaways: how many there are and how many still have no clip (`sve broll` fetches them)."""
    cues = [c for c in plan.get("cutaways") or [] if isinstance(c, dict) and c.get("type") == "broll"]
    return {"cues": len(cues), "without_clip": sum(1 for c in cues if not c.get("file"))} if cues else None


def _script(plan: dict) -> dict:
    """The explainer plan's sentences: how many were found in the recording, which are missing, which
    were said several times (and the take used) and which matched poorly."""
    sentences = [s for s in plan["sentences"] if isinstance(s, dict)]
    found = [s for s in sentences if not s.get("missing") and s.get("start") is not None]
    return {"file": plan.get("script"), "sentences": len(sentences), "found": len(found),
            "missing": [{"n": s.get("n"), "text": s.get("text", "")} for s in sentences if s not in found],
            "repeated": [{"n": s.get("n"), "takes": s["takes"], "start": s["start"], "score": s.get("score")}
                         for s in found if int(s.get("takes") or 1) > 1],
            "weak": [{"n": s.get("n"), "score": s["score"], "text": s.get("text", "")}
                     for s in found if float(s.get("score") or 1) < WEAK_MATCH]}


def _music_key(path: Path) -> str:
    return path.stem.replace(" ", "_")          # same key as application.music.music_analysis


def _music(svc: Services, project: Project, cfg: dict, latest: dict | None) -> dict:
    m = cfg.get("music", {})
    out = {"file": None, "start_at": m.get("start_at", 0), "bpm": None,
           "settings": {k: m[k] for k in MUSIC_SETTINGS if m.get(k) is not None}}
    if m.get("playlist") and m.get("file") not in (False, "none"):
        out["playlist"] = _playlist(svc, project, m)
        return out
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


def _playlist(svc: Services, project: Project, m: dict) -> dict:
    """music.playlist: number of songs, total minutes (of the songs already analyzed) and AutoMix on/off."""
    try:
        paths = svc.projects.find_playlist(m, project.dir)
    except UserError as exc:
        return {"songs": 0, "minutes": None, "automix": automix.settings(m)["enabled"], "error": str(exc)}
    known = [am.get("duration") for am in (svc.cache.load(project.cache, "music", _music_key(p)) or {}
                                            for p in paths)]
    known = [float(d) for d in known if isinstance(d, (int, float))]
    return {"songs": len(paths), "minutes": round(sum(known) / 60, 1) if known else None,
            "analyzed": len(known), "automix": automix.settings(m)["enabled"]}


def _version_entry(svc: Services, folder: Path) -> dict:
    summary = svc.projects.load_summary(folder) or {}
    files = sorted(p.name for p in svc.projects.list_files(folder) if not p.name.startswith(("_", "."))
                   and p.suffix.lower() in MEDIA_EXT)
    listen = next((d for d in (folder / "listen", folder / "escuchar") if svc.projects.exists(d)), None)
    audio = summary.get("audio") or {}
    return {
        "name": folder.name,
        "video": summary.get("video") or next((f for f in files if f.endswith(".mp4")), None),
        "duration": summary.get("duration_sec"), "shots": summary.get("shots"),
        "resolution": summary.get("resolution"), "lufs": audio.get("after_lufs"),
        "true_peak": audio.get("true_peak_dbtp"), "files": files,
        "check": svc.projects.exists(folder / "check.json"),
        "listen": len(svc.projects.list_files(listen)) if listen else 0,
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


def _source_token(s: dict) -> str:
    if s.get("folder"):
        return f"{s['index']} {s['file']} {s['photos']} photos"
    if s.get("photo"):
        return f"{s['index']} {s['file']} photo"
    return (f"{s['index']} {s['file']} {mmss(s['duration']) if s['duration'] is not None else '?'}"
            + (" HDR" if s.get("hdr") else "") + ("" if s.get("has_audio") in (True, None) else " mute"))


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
        parts.append(f"graphic:{e.get('template')}" if e["type"] == "graphic"
                     else f"{e['type']}<{','.join(map(str, e.get('from', [])))}")
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


def _script_lines(sc: dict | None) -> list[str]:
    if not sc:
        return []
    out = [f"SCRIPT {sc.get('file') or ''} · {sc['sentences']} sentences · {sc['found']} found".replace("  ·", " ·")]
    for s in sc["missing"][:MAX_SENTENCES]:
        out.append("  " + _message(f"⚠ sentence {s['n']} not found in the recording: {s['text']}"))
    if len(sc["missing"]) > MAX_SENTENCES:
        out.append(f"  … +{len(sc['missing']) - MAX_SENTENCES} more sentences not found")
    if sc["repeated"]:
        tokens = [f"sentence {s['n']}: {s['takes']} takes, used the one at {mmss(s['start'])}"
                  for s in sc["repeated"][:MAX_SENTENCES]]
        out += _pack(tokens)
        if len(sc["repeated"]) > MAX_SENTENCES:
            out.append(f"  … +{len(sc['repeated']) - MAX_SENTENCES} more sentences with several takes")
    for s in sc["weak"][:MAX_SENTENCES]:
        out.append("  " + _message(f"? sentence {s['n']} matched {float(s['score']):.0%}: {s['text']}"))
    return out


def _cutaway_lines(cu: dict | None) -> list[str]:
    if not cu:
        return []
    windows = sum(1 for c in cu["placed"] if c.get("pip"))
    out = [f"CUTAWAYS {cu['count']} · {len(cu['placed'])} placed (times before beat alignment)"
           + (f" · {cu['proposed']} proposed by the engine (~)" if cu.get("proposed") else "")
           + (f" · {windows} with the presenter's window (+pip)" if windows else "")]
    tokens = []
    for c in cu["placed"]:
        where = c.get("sentences")
        said = "" if not where else (f" (sentence {where[0]})" if where[0] == where[1]
                                     else f" (sentences {where[0]}–{where[1]})")
        tokens.append(f"{'~' if c.get('auto') else ''}{c['what']}{'*' if c.get('overlay') else ''}"
                      f"{'+pip' if c.get('pip') else ''} {c['start']:.1f}–{c['end']:.1f} s{said}")
    out += _pack(tokens, max_lines=MAX_SHOT_LINES)
    b = cu.get("broll")
    if b:
        out.append(f"  b-roll: {b['cues']} cues · {b['cues'] - b['without_clip']} with a clip"
                   + (f" · ⚠ {b['without_clip']} to fetch (sve broll <project>)" if b["without_clip"] else ""))
    for w in cu["warnings"][:MAX_MESSAGES]:
        out.append("  " + _message(f"⚠ {w}"))
    if len(cu["warnings"]) > MAX_MESSAGES:
        out.append(f"  … +{len(cu['warnings']) - MAX_MESSAGES} more warnings")
    return out


def report_lines(r: dict) -> list[str]:
    out = [f"PROJECT {r['project']} · recipe {r['recipe']} · language {r['language']}"]

    src = r["sources"]
    videos = [s for s in src if not s.get("folder") and not s.get("photo")]
    n_photos = sum(s.get("photos", 0) for s in src) + sum(1 for s in src if s.get("photo"))
    known = [s["duration"] for s in videos if s["duration"] is not None]
    head = f"SOURCES {len(src)}" + (f" · {n_photos} photos" if n_photos or len(videos) < len(src) else "")
    head += f" · {mmss(sum(known))} total" if known else ""
    if len(known) < len(videos):
        head += f" · {len(videos) - len(known)} not analyzed yet (sve analyze)"
    out.append(head)
    out += _pack([_source_token(s) for s in src])

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
        ph = p.get("photos")
        if ph:
            out.append(f"  {ph['count']} photo shots · {ph['min_dur']:.1f}–{ph['max_dur']:.1f} s each "
                       "(before fitting to the music)")
        out += _pack([_shot_token(e) for e in p.get("timeline", [])], max_lines=MAX_SHOT_LINES)
        if p.get("first_text"):
            out.append(f"  first text: {_message(p['first_text'])}")
        out += _script_lines(p.get("script"))
        out += _cutaway_lines(p.get("cutaways"))

    m = r["music"]
    pl = m.get("playlist")
    if pl is not None:
        if pl.get("error"):
            out.append(f"MUSIC playlist: {_message(pl['error'])}")
        else:
            minutes = (f"{pl['minutes']:.1f} min" + ("" if pl["analyzed"] == pl["songs"]
                                                     else f" ({pl['analyzed']} analyzed)")
                       if pl["minutes"] is not None else "length unknown (not analyzed)")
            out.append(f"MUSIC playlist of {pl['songs']} songs · {minutes} · "
                       f"AutoMix {'on' if pl['automix'] else 'off'}")
    elif m.get("error"):
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
    rv = r.get("review")
    if rv:
        out.append(f"MAKE {rv.get('version') or '?'}: {len(rv.get('fixed') or [])} fixed by the engine · "
                   f"{rv.get('renders', 1)} render(s)")
        out += review.decision_lines(rv.get("pending") or []) or ["  no decisions left"]
    return out


def report_json(r: dict) -> dict:
    """The report without the bulky raw summary of each version."""
    r = copy.deepcopy(r)
    for v in [r["versions"]["latest"], *r["versions"]["variants"]]:
        if v:
            v.pop("summary", None)
    return r
