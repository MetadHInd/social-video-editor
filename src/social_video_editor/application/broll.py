"""Use case: fetch third-party clips for the plan's `broll` cutaways (`sve broll <project>`).

For every broll cutaway without a file: the sources of `broll.sources` are tried in order (the
official channel of a listed brand only when the query names that brand); the candidates are ranked
(domain/footage.py), the best few are downloaded into <project>/footage/, the vision model — when it
is available — picks the clip and the piece of it that show the query best, and the plan entry gets
its `file` and `clip_start`. Every file is written down in footage/credits.json (source, page,
author, license, hash, date, query) and a caption with the credit is added to the plan the first
time each author or brand is on screen. `--dry-run` only lists what would be fetched.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from ..domain import director, footage
from ..domain import products as dp
from ..domain.cutaways import FREE_SECONDS, kind_of
from ..domain.footage import FootageError
from ..domain.models import Project, UserError
from ..domain.timeline import compute_timeline
from .cutaways import footage_records, place_cutaways
from .products import vision_available
from .services import Services

SEARCH_CACHE = "footage_search.json"        # in the project cache: {key: {"at": epoch, "candidates": [...]}}
SEARCH_TTL = 24 * 3600                      # stock libraries ask for search answers to be cached a day
PREVIEWS = "footage_preview"                # in the project cache: small copies of long videos, deleted after use
SHOWN = 3                                   # candidates listed per cue by --dry-run
PAD = 0.5                                   # seconds kept around the piece of a long video
AUTO_DIRECTOR = "director"                  # "_auto" of the b-roll the director proposed (domain/director.py)
LOOK_FPS = 1.0                              # frames per second the vision model looks at in a downloaded clip
MOTION_FRAMES = (0.15, 0.4, 0.65, 0.9)      # where in the chosen piece its motion is measured
INTEREST_WEIGHT = 1.0                       # how much "alive" counts against "on the subject" when ranking
STILL_SLACK = 0.02                          # a still may score this much under the minimums and still be tried
TEXT_FPS = 4.0                              # frames per second searched for printed text (ads change fast; it is cheap)
CROP_FRAMES = (0.2, 0.5, 0.8)               # where in the chosen piece the crops are compared
TEXT_SHARE = 0.25                           # a clip whose best piece shows text in more of its frames is not used
EXTRA_TRIES = 2                             # more downloads per source when candidates are turned down for text


def allowed_brands(svc: Services, cfg: dict) -> list[dict]:
    """The brands whose official channel may be used: `broll.brands` plus the pack's brands.yaml."""
    listed = list(footage.settings(cfg)["brands"] or [])
    pack = (cfg.get("graphics") or {}).get("pack")
    if pack and svc.packs is not None:
        listed += svc.packs.brands(pack)
    return footage.brands(listed)


class _Looker:
    """Looks at a downloaded clip: where it shows the query best (vision model, when it can be used),
    where it shows printed text (always: OpenCV), and where to crop it to fill the frame."""

    def __init__(self, svc: Services, project: Project, cfg: dict, W: int, H: int):
        self.svc, self.project, self.cfg, self.W, self.H = svc, project, cfg, W, H
        self._ok: bool | None = None

    @property
    def ok(self) -> bool:
        if self._ok is None:
            self._ok = vision_available(self.svc, self.cfg)
        return self._ok

    def judge(self, embeddings, query: str, said: str = "") -> tuple[list[float], list[float]]:
        """(relevance, interest) of pictures already embedded: how much each looks like the query or the
        sentence (the best of the two), and how alive it looks (lively − dull, domain/footage.py)."""
        v = self.vision_settings()
        emb = np.asarray(embeddings, np.float32)
        texts = [query] + ([said] if said and said != query else [])
        rel = (emb @ np.asarray(self.svc.visual.text_embeddings(texts, v), np.float32).T).max(axis=1)
        b = footage.settings(self.cfg)
        lively = (emb @ np.asarray(self.svc.visual.text_embeddings(list(b["lively"]), v), np.float32).T).max(axis=1)
        dull = (emb @ np.asarray(self.svc.visual.text_embeddings(list(b["dull"]), v), np.float32).T).max(axis=1)
        return [float(x) for x in rel], [float(x) for x in lively - dull]

    def vision_settings(self) -> dict:
        return {**dp.settings(self.cfg.get("vision"), self.cfg.get("language", "es")), "fps": LOOK_FPS}

    def look(self, path: Path, query: str, seconds: float, kind: str, said: str = "") -> dict:
        """{"start", "score" (relevance; None without the model), "interest" (None without the model),
        "text" (share of the piece's frames that show text), "focus" ((focus_x, focus_y), None when the clip is
        framed in the middle), "fit", "duration"}."""
        info = self.svc.probe.probe(path)
        duration = float(info.get("duration") or 0)
        spans = footage.crop_spans(info.get("width") or 0, info.get("height") or 0, self.W, self.H)
        t_text, boxes = self.svc.video.text_boxes(str(path), TEXT_FPS)
        raw = [min(footage.text_in(b, box) for _, _, box in spans) for b in boxes]
        texts, times, scores, v, query_emb = raw, t_text, None, None, None
        rel = alive = None
        if self.ok:
            v = self.vision_settings()
            t, emb = self.svc.visual.frame_embeddings(str(path), self.project.cache, f"footage_{Path(path).stem}", v)
            query_emb = np.asarray(self.svc.visual.text_embeddings([said or query], v), np.float32)
            rel, alive = self.judge(emb, query, said)
            times = list(t)
            scores = [r + INTEREST_WEIGHT * a for r, a in zip(rel, alive)]
        if self.ok and t_text:              # a frame "shows text" when any text frame of its second does
            texts = [max([tx for tt, tx in zip(t_text, raw) if abs(tt - x) <= 0.5 / LOOK_FPS] or [0.0]) for x in times]
        start, _, text = footage.choose_piece(times, scores, texts, seconds, duration, kind)
        score = lively = None
        if rel is not None:
            inside = [i for i, x in enumerate(times) if start - 1e-6 <= x < start + max(seconds, 1e-3)] or [0]
            score = sum(rel[i] for i in inside) / len(inside)
            lively = sum(alive[i] for i in inside) / len(inside)
        moving = self.svc.video.read_frames(str(path), [start + seconds * f for f in MOTION_FRAMES])
        motion = footage.motion_amount([np.asarray(f, np.float32).mean(axis=2) for f in moving if f is not None])
        focus, fit = None, None
        if len(spans) > 1:
            focus, fit, text = self._frame(path, info, spans, start, seconds, duration, t_text, boxes, query_emb, v)
        return {"start": start, "score": score, "text": text, "focus": focus, "fit": fit, "duration": duration,
                "motion": round(motion, 4),
                "interest": None if lively is None else footage.interest(lively, 0.0, motion)}

    def _frame(self, path, info, spans, start, seconds, duration, t_text, boxes, query_emb, v):
        """(focus, fit, text share) of a clip of another shape, from a few frames of the chosen piece: the
        subject whole (cropped around it, or shown large over a blurred copy when it is wider than the
        crop); a scene with detail everywhere is cropped where the query shows best with the least text."""
        end = min(start + seconds, duration)
        at = [start + (end - start) * f for f in CROP_FRAMES]
        inside = [i for i, x in enumerate(t_text) if start - 1e-6 <= x < end] or ([footage.nearest(t_text, start)] if t_text else [])
        frames = self.svc.video.read_frames(str(path), at)
        wide = spans[0][0] != spans[-1][0]
        shape = footage.framing(info.get("width") or 0, info.get("height") or 0, self.W, self.H,
                                footage.subject_span([footage.detail_profile(f[:, :, ::-1], 1 if wide else 0) for f in frames]))
        if shape["mode"] in ("crop", "fit"):
            f = shape["focus"]
            show = shape.get("fit") or (spans[0][2][2] - spans[0][2][0] if wide else spans[0][2][3] - spans[0][2][1])
            a = (1 - show) * f
            box = (a, 0.0, a + show, 1.0) if wide else (0.0, a, 1.0, a + show)
            shown = sum(1 for i in inside if footage.text_in(boxes[i], box) > footage.TEXT_MAX) / max(len(inside), 1)
            return ((f, 0.5) if wide else (0.5, f)), shape.get("fit"), shown
        focus, shown = self._crop(frames, spans, inside, boxes, query_emb, v)
        return focus, None, shown

    def _crop(self, frames, spans, inside, boxes, query_emb, v):
        """(focus, text share) of the crop that shows the query best with the least text."""
        texts = [[footage.text_in(boxes[i], box) for i in inside] or [0.0] for _, _, box in spans]
        sims = None
        if query_emb is not None:
            if frames:
                crops = [[np.ascontiguousarray(f[int(y0 * f.shape[0]):int(y1 * f.shape[0]),
                                                 int(x0 * f.shape[1]):int(x1 * f.shape[1]), ::-1]) for f in frames]
                         for _, _, (x0, y0, x1, y1) in spans]
                emb = np.asarray(self.svc.visual.image_embeddings([c for row in crops for c in row], v), np.float32)
                flat = list(emb @ query_emb[0])
                sims = [flat[i * len(frames):(i + 1) * len(frames)] for i in range(len(spans))]
        best = footage.pick_crop(sims, texts)
        shown = sum(1 for x in texts[best] if x > footage.TEXT_MAX) / max(len(texts[best]), 1)
        return (spans[best][0], spans[best][1]), shown


def _lengths(svc: Services, cfg: dict, plan: dict, W: int, H: int) -> dict[int, float]:
    """{cutaway index: seconds it is on screen}; {} for a plan that cannot be placed yet."""
    try:
        placed, _ = place_cutaways(svc, cfg, plan, compute_timeline(plan, cfg), W, H)
    except (UserError, KeyError, TypeError, ValueError):
        return {}
    return {c.index: c.end - c.start for c in placed}


def _search(svc: Services, cache: dict, src, query: str, limit: int, orientation: str, language: str,
            brand: dict | None, now: float) -> list[dict]:
    key = "|".join([src.name, language, orientation, (brand or {}).get("channel_id", ""), query])
    hit = cache.get(key)
    if isinstance(hit, dict) and now - float(hit.get("at", 0)) < SEARCH_TTL:
        return hit["candidates"]
    found = src.search(query, limit, orientation, language, brand)
    cache[key] = {"at": round(now), "candidates": found}
    return found


def _merged(found: list[dict]) -> list[dict]:
    """Results of several searches, each clip once (the first time it was found)."""
    seen, out = set(), []
    for c in found:
        key = (c.get("source"), str(c.get("id")))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _describe(c: dict) -> str:
    size = f"{c['width']}×{c['height']}" if c.get("width") and c.get("height") else "size unknown"
    title = f" “{c['title'][:40]}”" if c.get("title") else ""
    return (f"{c['source']} {c['id']}{title} · {c.get('author') or 'unknown author'} · {c.get('license') or '?'} · "
            f"{size} · {float(c.get('duration') or 0):.0f} s · {c.get('page') or ''}")


def fetch_broll(svc: Services, project: Project, dry_run: bool = False, force: bool = False) -> dict:
    """Fetches a clip for every broll cutaway that has none (all of them with force) → what happened:
    {"cues": [{"n", "query", "status", "file", "source", "author", "license", "seconds", "score", "notes",
    "candidates"}], "fetched", "missing", "dry_run"}. status: fetched | kept (already has its file) |
    candidates (dry run) | missing (nothing usable was found) | no_query."""
    cfg = project.cfg
    plan = svc.projects.load_plan(project)
    if plan is None:
        raise UserError(f"No plan.json in {project.dir}: run `sve plan {project.name}` first")
    cues = [(i, c) for i, c in enumerate(plan.get("cutaways") or []) if kind_of(c, i) == "broll"]
    out = {"cues": [], "fetched": 0, "missing": 0, "dry_run": dry_run}
    if not cues:
        return out
    s = footage.settings(cfg)
    sources = svc.footage or {}
    listed = allowed_brands(svc, cfg)
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    orientation = "portrait" if H > W else "landscape" if W > H else "square"
    language = str(cfg.get("language", "es"))
    lengths = _lengths(svc, cfg, plan, W, H)
    records = footage_records(svc, project)
    cache_path = project.cache / SEARCH_CACHE
    cache = svc.projects.read_json(cache_path)
    cache = cache if isinstance(cache, dict) else {}
    looker = _Looker(svc, project, cfg, W, H)
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    unready: dict[str, str] = {}
    changed = False

    said_in = {x.get("n"): str(x.get("text") or "") for x in plan.get("sentences") or [] if isinstance(x, dict)}
    taken = set()                           # clips the video already shows: never the same one in two places
    for _, c in cues:
        rec = records.get(str(c.get("file") or ""))
        if rec and not force and svc.projects.exists(project.dir / str(c["file"])):
            taken.add((rec.get("source"), _clip_id(str(c["file"]), rec)))
    for i, c in cues:
        query = str(c.get("query") or "").strip()
        entry = {"n": i, "query": query, "status": "missing", "file": c.get("file"), "notes": [], "candidates": []}
        out["cues"].append(entry)
        if c.get("file") and not force and svc.projects.exists(project.dir / str(c["file"])):
            entry["status"] = "kept"
            continue
        if not query:
            entry["status"] = "no_query"
            entry["notes"].append("no query to search with (query=\"…\"), and its file is missing")
            continue
        brand = footage.find_brand(c.get("brand") or query, listed)
        if c.get("brand") and brand is None:
            entry["notes"].append(f"brand '{c['brand']}' is not in the list of official channels (broll.brands)")
        on_screen = float(lengths.get(i) or c.get("seconds") or FREE_SECONDS)
        said = str(c.get("said") or said_in.get(c.get("at_sentence"), ""))
        searches = [(query, str(c.get("query_lang") or language))]
        if c.get("query_local") and str(c["query_local"]).strip() and c["query_local"] != query:
            searches.append((str(c["query_local"]).strip(), language))
        for name in ([str(c["source"])] if c.get("source") else s["sources"]):
            src = sources.get(name)
            if src is None:
                entry["notes"].append(f"{name}: unknown source ({', '.join(sorted(sources)) or 'none configured'})")
                continue
            if src.kind == footage.OFFICIAL and brand is None:
                continue                                    # no listed brand named: nothing official to look for
            if name not in unready:
                unready[name] = src.ready() or ""
            if unready[name]:
                entry["notes"].append(f"{name}: {unready[name]}")
                continue
            need = min(on_screen, footage.limit(src.kind, s))
            found, failed = [], None
            for q, lang in (searches[:1] if src.kind == footage.OFFICIAL else searches):
                try:
                    found += _search(svc, cache, src, q, int(s["search_limit"]), orientation, lang, brand, now)
                except FootageError as e:
                    failed = e
            found = _merged(found)
            if failed and not found:
                entry["notes"].append(f"{name}: {failed}")
                continue
            ranked, rejected = footage.rank(found, W, H, need, s, brand)
            ranked = [x for x in ranked if (x.get("source"), str(x.get("id"))) not in taken]   # never the same clip twice
            if not ranked:
                why = f" ({rejected[0][1]})" if rejected else ""
                entry["notes"].append(f"{name}: nothing usable among {len(found)} results{why}")
                continue
            if dry_run:
                entry["status"], entry["source"] = "candidates", name
                entry["candidates"] = [_describe(x) for x in ranked[:SHOWN]]
                break
            got = _take(svc, project, src, ranked, query, need, s, looker, entry, said)
            if got is None:
                continue
            cand, path, start, score, piece, focus, fit = got
            file = f"{footage.FOLDER}/{path.name}"
            old = str(c.get("file") or "")
            c["file"], c["clip_start"] = file, round(start, 3)
            for k in ("focus_x", "focus_y", "fit"):
                c.pop(k, None)
            if focus:                                       # where the clip is cropped to fill the frame
                c["focus_x"], c["focus_y"] = focus
            if fit:                                         # the subject is wider than a crop: shown whole, large
                c["fit"] = fit
            if on_screen > footage.limit(src.kind, s) + 0.05:
                c["seconds"] = footage.limit(src.kind, s)
                entry["notes"].append(f"on screen {on_screen:.1f} s → held to {c['seconds']:g} s "
                                      f"(broll.max_seconds.{src.kind})")
            records[file] = footage.record(cand, file, svc.projects.file_hash(path), query, stamp, piece)
            taken.add((cand.get("source"), str(cand.get("id"))))
            if old and old != file:
                records.pop(old, None)
            entry.update(status="fetched", file=file, source=name, author=cand.get("author"),
                         license=cand.get("license"), seconds=round(need, 2),
                         score=None if score is None else round(float(score), 3))
            changed = True
            break
        if entry["status"] == "missing" and not entry["notes"]:
            entry["notes"].append("no source could be tried (broll.sources)")
    if not dry_run:                         # no clip passed: an illustrated card with what is said, instead
        said = {int(x["n"]): x for x in plan.get("sentences") or [] if isinstance(x, dict) and x.get("words")}
        main = director.topic(list(said.values()))
        for e in out["cues"]:
            c = plan["cutaways"][e["n"]]
            if e["status"] != "missing" or not s.get("fallback_card", True):
                continue
            card = {k: v for k, v in (c.get("fallback") or {}).items() if v}
            if not card and c.get("at_sentence") is not None and int(c["at_sentence"]) in said:
                card = director.card(said[int(c["at_sentence"])], main=main)
            keep = {k: c[k] for k in ("at_sentence", "until_sentence", "from_word", "seconds", "note") if k in c}
            if card.get("template") and card.get("text"):
                plan["cutaways"][e["n"]] = {"type": "graphic", **card, **keep, "_auto": c.get("_auto") or "card"}
                e["status"] = "card"
                e["notes"].append(f"no clip passed: an illustrated card instead ({card['template']} “{card['text']}”)")
            elif c.get("_auto") == AUTO_DIRECTOR:
                plan["cutaways"][e["n"]] = None
                e["status"] = "dropped"
                e["notes"].append("no clip passed: removed (the presenter stays on camera)")
            else:
                continue
            changed = True
        plan["cutaways"] = [c for c in plan["cutaways"] if c is not None]
    out["fetched"] = sum(e["status"] == "fetched" for e in out["cues"])
    out["missing"] = sum(e["status"] in ("missing", "no_query") for e in out["cues"])
    svc.projects.write_json(cache_path, cache)
    svc.projects.remove(project.cache / PREVIEWS)
    if dry_run:
        return out
    credits_changed = _credit_overlays(plan, records, s)
    used = {str(c.get("file")) for c in plan.get("cutaways") or [] if c.get("file")}
    for file, rec in records.items():
        rec["used"] = file in used
    if records:
        svc.projects.write_json(project.dir / footage.FOLDER / footage.RECORD, [records[k] for k in sorted(records)])
    if changed or credits_changed:
        svc.projects.save_plan(project, plan)
    return out


def _by_stills(svc: Services, project: Project, src, ranked: list[dict], query: str, said: str, s: dict,
              looker: _Looker, entry: dict) -> list[dict]:
    """The candidates in the order their stills say (the ones that look like what is said and alive
    first); the ones whose still is clearly off are left out. Without the vision model, or without
    stills, the order is kept."""
    pool = ranked[:max(int(s["thumbnails"]), 0)]
    if not looker.ok or not pool:
        return ranked
    files = []
    for c in pool:
        try:
            files.append(src.preview(c, project.cache / PREVIEWS / "stills" / footage.file_name(c, ".jpg")))
        except (FootageError, OSError):
            files.append(None)
    shown = [k for k, f in enumerate(files) if f is not None]
    if not shown:
        return ranked
    emb = svc.visual.picture_embeddings([files[k] for k in shown], looker.vision_settings())
    rel, alive = looker.judge(emb, query, said)
    marks = {}
    for k, r, a in zip(shown, rel, alive):
        marks[k] = (r, a)
    keep, off = [], 0
    for k, c in enumerate(pool):
        if k not in marks:
            keep.append((float("-inf"), k, c))
            continue
        r, a = marks[k]
        if r < float(s["min_match"]) - STILL_SLACK or a < float(s["min_interest"]) - STILL_SLACK:
            off += 1
            continue
        keep.append((r + INTEREST_WEIGHT * a, -k, c))
    keep.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if off:
        entry["notes"].append(f"{src.name}: {off} of {len(pool)} left out by their still (off the subject or dull)")
    return [c for _, _, c in keep] + ranked[len(pool):]


def _take(svc: Services, project: Project, src, ranked: list[dict], query: str, need: float, s: dict,
          looker: _Looker, entry: dict, said: str = ""):
    """Downloads the best candidates of one source and keeps the one that shows the query best and no
    printed text → (candidate, file, start inside the file, score, piece of the original, focus) or None.
    Only the piece that is used is kept (plus PAD on each side): a stock clip is cut out of the download,
    an official one is downloaded again as that piece alone, in full quality."""
    folder = project.dir / footage.FOLDER
    several = max(int(s["candidates"]), 1) if looker.ok else 1      # nothing to compare them with: the first is taken
    tries = several + EXTRA_TRIES
    tried: list[tuple[float, int, dict, Path, dict]] = []
    downloads = 0
    ranked = _by_stills(svc, project, src, ranked, query, said, s, looker, entry)
    for k, cand in enumerate(ranked):
        if len(tried) >= several or downloads >= tries:
            break
        dest = project.cache / PREVIEWS / footage.file_name(cand)
        try:
            downloads += 1
            path = src.download(cand, dest, preview=src.kind == footage.OFFICIAL)
            seen = looker.look(path, query, need, src.kind, said)
        except (FootageError, UserError, OSError, ValueError, RuntimeError) as e:
            entry["notes"].append(f"{src.name} {cand.get('id')}: {e}")
            continue
        if seen["score"] is not None and seen["score"] < float(s["min_match"]):
            entry["notes"].append(f"{src.name} {cand.get('id')}: does not look like the query (match {seen['score']:.2f}, "
                                  f"below broll.min_match {float(s['min_match']):.2f}); not used")
            svc.projects.remove(path)
            continue
        if seen.get("interest") is not None and seen["interest"] < float(s["min_interest"]):
            entry["notes"].append(f"{src.name} {cand.get('id')}: looks dull (interest {seen['interest']:+.2f}, below "
                                  f"broll.min_interest {float(s['min_interest']):+.2f}); not used")
            svc.projects.remove(path)
            continue
        if seen["text"] > TEXT_SHARE and not s.get("text"):
            entry["notes"].append(f"{src.name} {cand.get('id')}: shows printed text (titles, captions) "
                                  f"in {seen['text']:.0%} of its best piece; not used (broll.text)")
            svc.projects.remove(path)
            continue
        value = float("-inf") if seen["score"] is None else float(seen["score"]) + INTEREST_WEIGHT * float(seen["interest"] or 0)
        tried.append((value, -k, cand, path, seen))
    if not tried:
        return None
    tried.sort(key=lambda x: (x[0], x[1]), reverse=True)
    chosen = None
    for _, _, cand, path, seen in tried:
        if chosen is not None:
            svc.projects.remove(path)
            continue
        start = seen["start"]
        piece = (max(start - PAD, 0.0), min(start + need + PAD, seen["duration"] or start + need + PAD))
        try:
            if src.kind == footage.OFFICIAL:
                final = src.download(cand, folder / footage.file_name(cand), piece=piece)
            else:
                final = svc.encoder.cut_clip(path, piece[0], piece[1], folder / footage.file_name(cand))
        except (FootageError, RuntimeError) as e:
            entry["notes"].append(f"{src.name} {cand.get('id')}: {e}")
            continue
        finally:
            svc.projects.remove(path)
        shown = _text_in_piece(svc, final, seen["focus"], seen.get("fit"), looker) if not s.get("text") else 0.0
        if shown > TEXT_SHARE:                      # seen in full quality, the piece still shows text
            entry["notes"].append(f"{src.name} {cand.get('id')}: its piece shows printed text in {shown:.0%} of its "
                                  "frames (checked in full quality); not used (broll.text)")
            svc.projects.remove(final)
            continue
        chosen = (cand, final, start - piece[0], seen["score"], piece, seen["focus"], seen.get("fit"))
    return chosen


def _text_in_piece(svc: Services, clip: Path, focus, fit, looker: "_Looker") -> float:
    """Share of the frames of a stored piece that show printed text where it will be on screen."""
    info = svc.probe.probe(clip)
    spans = footage.crop_spans(info.get("width") or 0, info.get("height") or 0, looker.W, looker.H)
    want = focus or (0.5, 0.5)
    box = min(spans, key=lambda sp: abs(sp[0] - want[0]) + abs(sp[1] - want[1]))[2]
    if fit and len(spans) > 1:
        wide = spans[0][0] != spans[-1][0]
        a = (1 - fit) * (want[0] if wide else want[1])
        box = (a, 0.0, a + fit, 1.0) if wide else (0.0, a, 1.0, a + fit)
    _, boxes = svc.video.text_boxes(str(clip), TEXT_FPS)
    return sum(1 for b in boxes if footage.text_in(b, box) > footage.TEXT_MAX) / max(len(boxes), 1)


def _clip_id(file: str, rec: dict) -> str:
    """The source's id of a recorded clip (older records only have it in the file name: <source>_<id>_…)."""
    if rec.get("id"):
        return str(rec["id"])
    name = Path(file).stem
    prefix = f"{rec.get('source')}_"
    return name[len(prefix):].split("_")[0] if name.startswith(prefix) else ""


def _credit_overlays(plan: dict, records: dict[str, dict], s: dict) -> bool:
    """Rewrites the credit captions (`_auto: credit`): a lower third the first time each author or
    brand's footage is on screen. → whether the plan changed."""
    cutaways = plan.get("cutaways") or []
    out, seen = [], set()
    for c in cutaways:
        if c.get("_auto") == footage.CREDIT_AUTO:
            continue
        out.append(c)
        rec = records.get(str(c.get("file") or "")) if s.get("credit") and c.get("type") == "broll" else None
        if rec is None:
            continue
        label = footage.credit_label(rec)
        if label in seen:
            continue
        seen.add(label)
        where = {"at_sentence": c["at_sentence"]} if c.get("at_sentence") is not None else {"start": c.get("start", 0)}
        if c.get("from_word"):                      # with the clip, not before it
            where["from_word"] = c["from_word"]
        out.append({"type": "graphic", "template": "lower_third", "kicker": "Video", "text": label, **where,
                    "seconds": float(s["credit_seconds"]), "_auto": footage.CREDIT_AUTO})
    if out == cutaways:
        return False
    plan["cutaways"] = out
    return True


def broll_lines(result: dict) -> list[str]:
    """What `sve broll` prints."""
    cues = result["cues"]
    if not cues:
        return ["The plan has no broll cutaways (a [broll query=\"…\"] cue in the script adds one)."]
    kept = sum(e["status"] == "kept" for e in cues)
    head = (f"B-roll: {len(cues)} cues · {result['fetched']} fetched · {kept} already had a clip · "
            f"{result['missing']} without a clip")
    if result["dry_run"]:
        head = f"B-roll (dry run, nothing downloaded): {len(cues)} cues · {kept} already have a clip"
    lines = [head]
    for e in cues:
        what = f"  [{e['n']}] “{e['query']}”" if e["query"] else f"  [{e['n']}] {e.get('file')}"
        if e["status"] == "kept":
            lines.append(f"{what}: keeps {e['file']}")
        elif e["status"] == "fetched":
            score = f", match {e['score']:.2f}" if e.get("score") is not None else ""
            lines.append(f"{what}: {e['file']} — {e['source']}, {e.get('author') or 'unknown author'}, "
                         f"{e.get('license') or '?'} ({e['seconds']:g} s{score})")
        elif e["status"] == "candidates":
            lines.append(f"{what}: would look at")
            lines += [f"      {c}" for c in e["candidates"]]
        elif e["status"] in ("dropped", "card"):
            lines.append(f"{what}: no clip found")
        else:
            lines.append(f"{what}: ⚠ no clip")
        lines += [f"      · {n}" for n in e["notes"]]
    if not result["dry_run"] and result["fetched"]:
        lines.append(f"Record: {footage.FOLDER}/{footage.RECORD} · next: sve render <project> --draft")
    return lines
