"""The engine's own review of an explainer video (`sve make`): what a viewer would notice, found in the
plan as it is placed on the video and in the automatic check of a render. Each finding comes with a
fixed remedy the engine applies by itself, or — when no remedy is safe — a decision left for a person.

    code                    found in   remedy
    cutaway_left_out        plan       proposed by the engine: removed · written by hand: decision
    cutaway_too_short       plan       less than it takes to come in and be read → proposed: removed ·
                                       by hand: decision
    text_too_long_to_read   plan       its words need longer than it is shown (`review.read_cps`) → a card the
                                       engine wrote is shortened · by hand: decision
    cutaway_too_long        plan       a full-frame graphic or photo over `review.graphic_max` → it ends earlier
                                       and the presenter comes back for the rest of the sentence (a cue
                                       spanning several sentences on purpose: decision)
    camera_too_long         plan       the presenter alone longer than the director allows (+ `camera_slack`)
                                       → the director proposes footage there · nothing to propose: decision
    sentence_unmatched      plan       a script sentence not found in the recording → decision
    frozen_picture          render     a clip that stops moving before its slot ends → it ends there ·
                                       anything else: decision
    text_outside_safe_zone  render     decision
    render_error            render     an error of the check (loudness, black frames, clipping…) → decision

With `review.ladder` (on by default) a finding may have several remedies, tried in order, one per round
(a remedy that did not hold is never tried again, the next one is):

    frozen_picture (clip)   another clip for the same cue (the next candidate; `review.clip_tries`) → it
                            ends where it stops → a drawing of its sentence (engine cues) → removed
    broll_low_relevance     a fetched clip whose match score is under `review.clip_match` (0 = off):
                            another clip → a drawing → removed (engine cues; by hand: decision)
    text_too_long_to_read   an engine card shortened → another kind of drawing for that sentence (the
                            director's next proposal) → removed
    face_out_of_frame       the presenter's face cut by the crop of a shot (`review.faces`): framed on
                            the face again → plain framing (no punch-in, no slow zoom) → decision
    text_outside_safe_zone  a plan text moved inside the safe zone (its `y`) · subtitles: decision
    text_too_short          a plan text shown less than it takes to read: kept to the end of the next
                            shot → its second line dropped → decision
    music_covers_voice      the music closer to the voice than `check.music_min_under_voice_lu`: the
                            music lowered by what was missing (a setting of this `sve make`, see below)
    loudness_off            decision (the mastering already aims at the target)

A remedy is applied once: when the same finding comes back after it, the next one is tried, and with
none left it becomes a decision. Every plan entry a remedy changed keeps the codes in "_fixed" (private:
it does not change the render). Remedies that are settings (the music level) are not in the plan: they
are returned in `adjust` and `sve make` keeps them in review.json.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import director
from . import pip as pip_window
from .explainer import FACE_MIN_RATIO, face_focus
from .script import tokens
from .cutaways import MIN_BETWEEN, Cutaway
from .decide import Decide, Option, Question, ask
from .graphics.scene import TEMPLATES

REVIEW_FILE = "review.json"     # <project>/review.json: what the last `sve make` fixed and left
DEFAULTS = {"rounds": 3, "read_cps": 15.0, "graphic_max": 6.0, "camera_slack": 1.5,
            "ladder": True, "clip_tries": 2, "clip_match": 0.0, "faces": True, "keep_attempts": False}
ENGINE = ("director", "card", "support", "review")    # "_auto" of the cutaways the engine proposed (it may remove them)
READ_KEYS = ("kicker", "title", "text", "label", "prefix", "value", "unit", "sub", "data", "items", "nodes",
             "name", "specs", "price", "outlet", "date")
NEGATIONS = {"no", "nunca", "jamás", "jamas", "ni", "tampoco", "nada", "not", "never", "nothing", "don't", "doesn't",
             "isn't", "can't", "won't"}
FEWEST_WORDS = 2            # a shortened card keeps at least this many words
FEWEST_ITEMS = 2            # …and a list at least this many items
HEARD_SHARE = 0.3           # a word the voice says while it is shown is read along: it counts this share
SLACK = 0.25                # seconds a measure may run over its limit before it counts
FACE_BACK = 1.5             # a graphic cut short leaves the presenter at least this long (domain/director.MIN_FACE)
CLOSING = "the closing sentence stays on camera"
SHORTER_VERSIONS = 4        # the rules' shorter text and up to three others the decider may choose from
SHORTEN_CONFIDENCE = 0.6    # how sure the rules are that cutting the last words reads best
SHORTEN_PROMPT = {"es": "¿Cuál de estos textos más cortos se entiende mejor y dice lo mismo?",
                  "en": "Which of these shorter texts reads best and still says the same thing?"}
_CLAUSE = re.compile(r"[,;:]\s+")
CARD_CONTENT = ("text", "items", "value", "data", "nodes", "rows", "lines", "image")   # a drawing shows something (application/broll.py)
FOOTAGE = "footage/"        # fetched clips live there (domain/footage.FOLDER): another one can be fetched
CARD_KEEP = ("at_sentence", "until_sentence", "from_word", "seconds", "note", "_decided")   # a drawing keeps where it was
FACE_SLACK = 0.1            # share of the face's height that may be cut before it counts
FACE_WIDTH = 0.75           # a face is about this wide for its height
SAFE_MARGIN = 0.01          # a text moved inside the safe zone goes this much further in
_EDGE = re.compile(r"(top|bottom) ([\d.]+)·H [<>] ([\d.]+)")
LEGACY = ("drop", "set", "add")     # remedies of before the ladder: their first one is marked by the key alone
MUSIC_MARGIN = 1.0          # dB lowered beyond what was missing


@dataclass
class Issue:
    code: str
    message: str
    t: float | None = None                  # second of the video where it is
    cutaway: int | None = None              # index in plan["cutaways"]
    fix: dict | None = None                 # {"drop": True} | {"set": {...}} | {"add": [cutaways]} | the ones below
    options: list[str] = field(default_factory=list)       # what a person can do (when there is no fix)
    key: str = ""                           # the same finding in another round has the same key
    more: list[dict] = field(default_factory=list)         # the next remedies, tried in order when `fix` did not hold
    # more kinds of fix: {"another_clip": file} · {"card": spec} · {"shot": k, "set"|"plain"} ·
    # {"text": k, "set"|"pop"} · {"settings": {section: {key: value}}}

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.t is not None:
            out["t"] = round(self.t, 2)
        if self.cutaway is not None:
            out["cutaway"] = self.cutaway
        if self.options:
            out["options"] = self.options
        return out


@dataclass
class Layout:
    """The plan placed on the video (application/make.py builds it without rendering)."""
    placed: list[Cutaway]                   # domain/cutaways.resolve
    minimum: dict[int, float]               # plan cutaway index → seconds it needs to come in and be read
    spans: dict[int, tuple[float, float]]   # sentence → (start, end) in the video
    total: float
    texts: list[tuple[int, float, float]] = field(default_factory=list)    # plan text index → (start, end) in the video


def settings(cfg: dict) -> dict:
    s = (cfg or {}).get("review") or {}
    return {k: s.get(k, v) if s.get(k) is not None else v for k, v in DEFAULTS.items()}


def by_engine(cutaway: dict) -> bool:
    return str(cutaway.get("_auto") or "") in ENGINE


def reading(spec: dict) -> str:
    """The words a graphic shows (what a viewer has to read)."""
    parts = []
    for k in READ_KEYS:
        v = spec.get(k)
        if isinstance(v, (list, tuple)):
            parts += [str(x) for x in v]
        elif v is not None and str(v).strip():
            parts.append(str(v))
    return " ".join(p.strip() for p in parts if p.strip())


def _stem(word: str) -> str:
    """Enough of a word to know it when said in another form ("usa" / "usan", "gasta" / "gastan")."""
    t = "".join(tokens(word))
    return (t[:-1] if len(t) > 3 and t.endswith(("n", "s")) else t)[:4]


def heard(plan: dict, sentences: tuple[int, int] | None) -> set[str]:
    """The words said while a cutaway is on screen (their first letters: "usan" and "usa" are one word)."""
    if sentences is None:
        return set()
    a, b = sentences
    return {_stem(str(w[0])) for x in plan.get("sentences") or [] if a <= int(x.get("n", -1)) <= b
            for w in x.get("words") or []} - {""}


def read_seconds(spec: dict, cps: float, said: set[str] = frozenset()) -> float:
    """Seconds a viewer needs to read a graphic. Words the voice says at the same time are read along
    with it: they count HEARD_SHARE of their length."""
    total = 0.0
    for w in reading(spec).split():
        total += (len(w) + 1) * (HEARD_SHARE if _stem(w) in said else 1.0)
    return max(total - 1, 0.0) / max(float(cps), 1.0)


# ── the plan ─────────────────────────────────────────────────────────────────

def review_plan(plan: dict, layout: Layout, cfg: dict, decide: Decide | None = None) -> list[Issue]:
    """Findings in the plan as placed on the video, in time order."""
    r = settings(cfg)
    cutaways = plan.get("cutaways") or []
    found: list[Issue] = []
    shown = {c.index: c for c in layout.placed}
    for i, c in enumerate(cutaways):
        if i in shown:
            continue
        label = _label(c)
        engine = by_engine(c)
        found.append(Issue("cutaway_left_out", f"cutaway {i} ({label}) does not fit in the video", None, i,
                           {"drop": True} if engine else None,
                           [] if engine else ["anchor it to another sentence", "remove it"], f"left:{_where(c)}"))
    for c in layout.placed:
        source = cutaways[c.index]
        length, need = c.end - c.start, layout.minimum.get(c.index, 0.0)
        engine = by_engine(source)
        if c.kind == "broll" and float(r["clip_match"]) > 0 and source.get("_match") is not None \
                and float(source["_match"]) < float(r["clip_match"]):
            steps = clip_steps(plan, c.index, r, cfg, decide)
            found.append(Issue("broll_low_relevance", f"the clip {c.label} at {c.start:.1f} s matches its search only "
                               f"{float(source['_match']):.2f} (review.clip_match {float(r['clip_match']):.2f})", c.start,
                               c.index, steps[0] if steps else None,
                               [] if steps else ["choose another clip (sve broll --force)", "leave it"],
                               f"match:{_where(source)}", steps[1:]))
        if length < need - SLACK:
            earlier = sooner(plan, layout, c, need - length) if engine else None
            found.append(Issue("cutaway_too_short", f"{c.label} at {c.start:.1f} s: {length:.1f} s on screen, it needs "
                               f"{need:.1f} s to come in and be read", c.start, c.index,
                               ({"set": {"from_word": earlier}} if earlier else {"drop": True}) if engine else None,
                               [] if engine else [_longer(source), "remove it"], f"short:{_where(source)}"))
            continue
        if c.overlay or c.kind == "broll":
            continue
        words = reading(c.spec)
        need_read = read_seconds(c.spec, r["read_cps"], heard(plan, c.sentences))
        if words and need_read > length + SLACK:
            short = shorten(c.spec, length * float(r["read_cps"]), decide,
                            str((cfg or {}).get("language") or "es")) if engine else None
            fix, more = {"set": short} if short else ({"drop": True} if engine else None), []
            if engine and r["ladder"]:              # shortened → another kind of drawing → removed
                other = drawing(plan, source, cfg, decide)
                steps = ([{"set": short}] if short else []) + ([{"card": other}] if other else []) + [{"drop": True}]
                fix, more = steps[0], steps[1:]
            found.append(Issue("text_too_long_to_read", f"{c.label} at {c.start:.1f} s: {len(words)} characters need "
                               f"{need_read:.1f} s to read, it is shown {length:.1f} s", c.start, c.index, fix,
                               [] if engine else ["shorten its text", _longer(source)],
                               f"read:{_where(source)}", more))
        limit = float(r["graphic_max"])
        if limit > 0 and length > limit + SLACK:
            seconds = round(max(need, min(limit, length - FACE_BACK)), 2)
            one = engine or (c.sentences is not None and c.sentences[0] == c.sentences[1])
            fix = {"set": {"seconds": seconds}} if one and length - seconds >= MIN_BETWEEN else None
            found.append(Issue("cutaway_too_long", f"{c.label} at {c.start:.1f} s stays {length:.1f} s (most "
                               f"{limit:g} s): the picture rests too long", c.start, c.index, fix,
                               [] if fix else [f"split it (seconds={limit:g} and another cue for the rest)",
                                               "leave it"], f"long:{_where(source)}"))
    found += camera_stretches(plan, layout, cfg, decide)
    found += texts_too_short(plan, layout, cfg)
    for s in plan.get("sentences") or []:
        if isinstance(s, dict) and s.get("missing"):
            found.append(Issue("sentence_unmatched", f"sentence {s.get('n')} was not found in the recording: "
                               f"«{str(s.get('text') or '')[:60]}»", None, None, None,
                               ["record it again and run sve make", "remove it from the script", "leave it out"],
                               f"missing:{s.get('n')}"))
    return sorted(found, key=lambda x: (x.t is None, x.t or 0.0))


def sooner(plan: dict, layout: Layout, c: Cutaway, missing: float) -> list | None:
    """A cutaway that starts at a word (from_word) moved `missing` seconds earlier — to the word said
    then — when the presenter is still seen FACE_BACK seconds after the picture before it, and it stays
    inside its sentence. → the new from_word, or None."""
    entry = (plan.get("cutaways") or [])[c.index]
    if not entry.get("from_word") or c.sentences is None or c.sentences[0] not in layout.spans:
        return None
    source, t = entry["from_word"]
    before = max([x.end for x in layout.placed if not x.overlay and not x.pip and x.end <= c.start + 1e-6], default=0.0)
    lowest = max(layout.spans[c.sentences[0]][0] + director.LEAD_IN, before + FACE_BACK)
    start = c.start - missing
    if start < lowest - 1e-6:
        return None
    target = float(t) - missing
    sentence = next((x for x in plan.get("sentences") or [] if x.get("n") == c.sentences[0]), {})
    said = [float(w[1]) for w in sentence.get("words") or [] if target - 0.6 <= float(w[1]) <= target + 1e-6]
    word = max(said) if said and float(t) - max(said) <= missing + 0.6 and c.start - (float(t) - max(said)) >= lowest - 1e-6 \
        else None
    return [source, round(word if word is not None else target, 3)]


def camera_stretches(plan: dict, layout: Layout, cfg: dict, decide: Decide | None = None) -> list[Issue]:
    """Stretches where the presenter is alone (no full-frame cutaway) for longer than the director allows.
    A sentence of a source without picture (a voice memo, `pictureless`) allows no camera time at all,
    the closing one included."""
    d = director.settings(cfg)
    if not d["auto_broll"] or not layout.spans:
        return []
    slack = float(settings(cfg)["camera_slack"])
    blind = director.pictureless_sources(plan)
    unseen = {int(x["n"]) for x in plan.get("sentences") or [] if isinstance(x, dict) and x.get("source") in blind}
    covers = sorted((c.start, c.end) for c in layout.placed if not c.overlay and not c.pip)
    closing = max(layout.spans)
    last = layout.spans[closing]
    out, t = [], 0.0
    for a, b in covers + [(layout.total, layout.total)]:
        end = min(a, last[0]) if b >= layout.total - 1e-6 and closing not in unseen else a   # the closing sentence stays on camera
        inside = [n for n, (s0, s1) in layout.spans.items() if s0 < end - 0.05 and s1 > t + 0.05]
        allowed = (0.0 if any(n in unseen for n in inside) else float(d["opening"] if t < 0.05 else d["max_camera"])) + slack
        if end - t > allowed:
            fresh = [c for c in director.propose(plan, cfg, overlay_templates(), decide=decide) if c.get("at_sentence") in inside]
            out.append(Issue("camera_too_long", f"the presenter is alone on camera {end - t:.1f} s from {t:.1f} s "
                             f"(most {allowed - slack:g} s)", t, None, {"add": fresh} if fresh else None,
                             [] if fresh else ["add a [graphic] or [broll] cue to that sentence", "leave it"],
                             f"camera:{inside[0] if inside else round(t)}"))
        t = max(t, b)
    return out


def clip_steps(plan: dict, i: int, r: dict, cfg: dict | None = None, decide: Decide | None = None,
               shorter: dict | None = None) -> list[dict]:
    """The remedies of a clip that does not work, in order: another clip for the same cue (the next
    candidate, while `review.clip_tries` allows) → `shorter` (it ends earlier) → a drawing of its sentence
    → removed. A cue written by hand only gets another clip (or ends earlier): the rest is a decision."""
    c = (plan.get("cutaways") or [])[i]
    steps = []
    if r["ladder"] and c.get("query") and str(c.get("file") or "").startswith(FOOTAGE) \
            and len(c.get("_rejected") or []) < int(r["clip_tries"]):
        steps.append({"another_clip": str(c["file"])})
    if shorter:
        steps.append(shorter)
    if by_engine(c):
        other = drawing(plan, c, cfg, decide) if r["ladder"] else None
        steps += ([{"card": other}] if other else []) + [{"drop": True}]
    return steps


def drawing(plan: dict, c: dict, cfg: dict | None = None, decide: Decide | None = None) -> dict | None:
    """Another kind of drawing for the sentence of cutaway `c`: the drawing a director's b-roll cue
    carries (`fallback`), else the director's card for that sentence (domain/director.card) of a template
    it has not had yet (`_was`). None when there is none (no sentence, nothing new to draw)."""
    graphic = str(c.get("type") or "graphic") == "graphic"
    was = {*(c.get("_was") or []), *([str(c.get("template") or "")] if graphic else [])}
    spare = {k: v for k, v in (c.get("fallback") or {}).items() if v}
    if spare.get("template") and spare["template"] not in was and any(spare.get(k) for k in CARD_CONTENT):
        return spare
    found = [x for x in plan.get("sentences") or [] if isinstance(x, dict) and x.get("words") and not x.get("missing")]
    sentence = next((x for x in found if x.get("n") == c.get("at_sentence")), None)
    if sentence is None:
        return None
    at = float(c["from_word"][1]) if c.get("from_word") else None
    card = director.card(sentence, at, main=director.topic(found), last=str(c.get("template") or "") or None,
                         decide=decide, language=(cfg or {}).get("language"), opts=director.settings(cfg or {}))
    return card if card.get("template") not in was and any(card.get(k) for k in CARD_CONTENT) else None


def texts_too_short(plan: dict, layout: Layout, cfg: dict) -> list[Issue]:
    """Plan texts (unboxing cards, hooks…) shown for less than it takes to read them (`review.read_cps`).
    Remedy: a text anchored to a shot stays to the end of the next one when that shot has no text of its
    own; anything else is a decision. Only with `review.ladder`."""
    r = settings(cfg)
    texts = plan.get("texts") or []
    if not r["ladder"] or not layout.texts:
        return []
    shots = len(plan.get("shots") or [])
    anchored = {int(t["shot"]) for t in texts if isinstance(t, dict) and t.get("shot") is not None}
    out = []
    for k, start, end in layout.texts:
        tx = texts[k]
        need = read_seconds({"text": tx.get("text"), "sub": tx.get("description")}, r["read_cps"])
        if need <= end - start + SLACK:
            continue
        fix = None
        if tx.get("shot") is not None:
            last = int(tx.get("until_shot", tx["shot"]))
            if last + 1 < shots and last + 1 not in anchored:
                fix = {"text": k, "set": {"until_shot": last + 1}}
        out.append(Issue("text_too_short", f"text «{str(tx.get('text') or '')[:30]}» at {start:.1f} s is shown "
                         f"{end - start:.1f} s, it needs {need:.1f} s to be read", start, None, fix,
                         [] if fix else ["shorten it", "show it longer (until_shot / end)", "leave it"],
                         f"textshort:{k}:{str(tx.get('text') or '')[:20]}"))
    return out


def faces_cut(plan: dict, infos: list[dict], analyses: list[dict], cfg: dict) -> list[Issue]:
    """Camera shots whose crop (framing, punch-in, slow zoom at its closest) cuts the presenter's face —
    the face found in the source by the analysis (`framing.face`). Remedies: framed on the face again
    (domain/explainer.face_focus) → plain framing (no punch-in, no slow zoom) → a decision."""
    r = settings(cfg)
    if not r["faces"]:
        return []
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    framing = cfg.get("framing") or {}
    mode = str(framing.get("mode", "auto"))
    margin = float(framing.get("zoom_margin", 1.25))
    out = []
    for k, s in enumerate(plan.get("shots") or []):
        src = s.get("source")
        if not isinstance(src, int) or not 0 <= src < min(len(infos), len(analyses)) or s.get("type") not in (None, "video"):
            continue
        face = (analyses[src] or {}).get("face")
        info = infos[src] or {}
        if not face or float(face.get("ratio", 0)) < FACE_MIN_RATIO or not info.get("width") or not info.get("height"):
            continue
        focus = (float(s.get("focus_x", framing.get("focus_x", 0.5))), float(s.get("focus_y", framing.get("focus_y", 0.5))))
        cut, zoom = face_cut(face, info, W, H, focus, s, mode, margin)
        if cut <= FACE_SLACK:
            continue
        steps = []
        best = face_focus(face, info["width"], info["height"], W, H, mode)
        if abs(best[0] - focus[0]) > 0.01 or abs(best[1] - focus[1]) > 0.01:
            steps.append({"shot": k, "set": {"focus_x": best[0], "focus_y": best[1]}})
        if zoom > 1.0 + 1e-6:
            steps.append({"shot": k, "plain": True})
        start = float(s.get("start") or 0)
        out.append(Issue("face_out_of_frame", f"shot {k} (source {src} at {start:.1f} s): the frame cuts "
                         f"{min(cut, 1.0):.0%} of the presenter's face", None, None, steps[0] if steps else None,
                         [] if steps else ["frame it by hand (focus_x / focus_y, zoom)", "leave it"],
                         f"face:{src}:{start:.1f}", steps[1:]))
    return out


def face_cut(face: dict, info: dict, W: int, H: int, focus: tuple[float, float], shot: dict, mode: str,
             margin: float) -> tuple[float, float]:
    """(the largest share of the face outside the frame, the closest zoom of the shot): the face's box
    placed in the frame (domain/pip.face_in_frame), then zoomed about the focus as the render does."""
    f = pip_window.face_in_frame(face, info["width"], info["height"], W, H, focus, mode, min_ratio=0.0)
    if f is None:
        return 0.0, 1.0
    zoom = float(shot.get("zoom", 1.0)) + sum(float(e.get("amount", 0.06)) for e in shot.get("effects") or []
                                               if e.get("type") in ("push_in", "pull_out"))
    zoom = min(zoom, margin) if zoom > 1 else 1.0
    half_h = float(f["size"]) / 2
    half_w = FACE_WIDTH * float(f["size"]) * H / W / 2
    if half_h <= 0:
        return 0.0, zoom

    def edges(c: float, half: float, at: float) -> tuple[float, float]:
        return (c - half - at) * zoom + at, (c + half - at) * zoom + at

    x0, x1 = edges(float(f["x"]), half_w, focus[0])
    y0, y1 = edges(float(f["y"]), half_h, focus[1])
    across = max(-x0, x1 - 1, 0.0) / max(x1 - x0, 1e-6)
    down = max(-y0, y1 - 1, 0.0) / max(y1 - y0, 1e-6)
    return max(across, down), zoom


def overlay_templates() -> set[str]:
    """Templates drawn over the picture (they do not hide the presenter)."""
    return {name for name, tpl in TEMPLATES.items() if getattr(tpl, "overlay", False)}


def shorten(spec: dict, chars: float, decide: Decide | None = None, language: str = "es") -> dict | None:
    """The text of an engine card cut to about `chars` characters (None when it cannot be shortened
    and still say something): a title or scene keeps its first words, a list its first items, a figure
    drops the line under it, then its label. A title or scene that can also be cut another way (a
    clause of it, its last words) asks the decider which shorter text reads best (`shorten`)."""
    template = str(spec.get("template") or "")
    if template in ("bars", "diagram", "list") and spec.get("title"):
        return {"title": ""}
    if template in ("title", "scene", "headline") and spec.get("text"):
        words = str(spec["text"]).rstrip("…").split()
        while len(words) > FEWEST_WORDS and len(" ".join(words) + "…") > chars:
            words.pop()
        text = " ".join(words).rstrip(",;:") + ("…" if len(words) < len(str(spec["text"]).rstrip("…").split()) else "")
        rest = len(reading({**spec, "text": ""}))
        if text == spec["text"] or len(text) + rest > chars + 8:
            return None
        longest = max(chars, len(text))
        versions = [text]
        for v in _other_versions(str(spec["text"]), chars):
            if (v not in versions and v != spec["text"] and len(v.split()) >= FEWEST_WORDS and len(v) <= longest
                    and len(v) + rest <= chars + 8 and len(versions) < SHORTER_VERSIONS):
                versions.append(v)
        if len(versions) >= 2:
            q = Question("shorten", "choice", SHORTEN_PROMPT.get(language, SHORTEN_PROMPT["en"]),
                         tuple(Option(v, v) for v in versions), str(spec["text"]), (text,), SHORTEN_CONFIDENCE, language)
            text = ask(decide, q).value or text
        return {"text": text}
    if template == "list" and spec.get("items"):
        items = [x.strip() for x in str(spec["items"]).split(";") if x.strip()]
        while len(items) > FEWEST_ITEMS and len(" ".join(items)) > chars:
            items.pop()
        return {"items": "; ".join(items)} if len(items) < len(str(spec["items"]).split(";")) else None
    if template == "number":
        for k in ("sub", "label"):
            if spec.get(k):
                return {k: ""}
    return None


def _other_versions(text: str, chars: float) -> list[str]:
    """Other shorter versions of a card's text than its first words: each clause (split at a comma,
    semicolon or colon), then its last words that fit in `chars`."""
    whole = text.rstrip("…").strip()
    lead = len(whole) - len(whole.lstrip("¿¡\"'«("))
    upper = whole[lead:lead + 1].isupper()
    said = {w.strip(".,;:¿?¡!").lower() for w in whole.split()}

    def tidy(part: str) -> str:
        part = part.strip().rstrip(",;:").strip()
        if part.endswith("?") and "¿" in whole and not part.startswith("¿"):
            part = "¿" + part                   # a question keeps its opening mark
        at = len(part) - len(part.lstrip("¿¡\"'«("))
        return part[:at] + part[at:at + 1].upper() + part[at + 1:] if upper else part

    def keeps_meaning(part: str) -> bool:     # a version that drops "no" / "never" says the opposite
        kept = {w.strip(".,;:¿?¡!").lower() for w in part.split()}
        return not ((said & NEGATIONS) - kept)

    out = [tidy(part) for part in _CLAUSE.split(whole)][:3] if _CLAUSE.search(whole) else []
    words = whole.split()
    tail = list(words)
    while len(tail) > FEWEST_WORDS and len(" ".join(tail)) > chars:
        tail.pop(0)
    if len(tail) < len(words):
        out.append(tidy(" ".join(tail)))
    return [v for v in out if v and keeps_meaning(v)]


# ── the render ───────────────────────────────────────────────────────────────

def review_render(plan: dict, layout: Layout, check: dict, cfg: dict | None = None,
                  decide: Decide | None = None) -> list[Issue]:
    """Findings of `sve check` (check.json) that point at something on screen or in the sound. With
    `cfg` (and `review.ladder`) they get the remedies of the ladder; without it, those of before."""
    metrics = check.get("metrics") or {}
    cutaways = plan.get("cutaways") or []
    r = settings(cfg) if cfg is not None else {**DEFAULTS, "ladder": False}
    found: list[Issue] = []
    for a, b in (metrics.get("video") or {}).get("freeze") or []:
        under = next((c for c in layout.placed if not c.overlay and c.start - 0.1 <= a < c.end), None)
        if under is not None and under.kind == "broll":
            need = layout.minimum.get(under.index, 0.0)
            moving = a - under.start
            source = cutaways[under.index]
            shorter = {"set": {"seconds": round(moving, 2)}} if moving >= need else None
            if r["ladder"]:
                steps = clip_steps(plan, under.index, r, cfg, decide, shorter)
                fix, more = (steps[0], steps[1:]) if steps else (None, [])
            else:
                fix, more = shorter or ({"drop": True} if by_engine(source) else None), []
            found.append(Issue("frozen_picture", f"the clip {under.label} stops moving at {a:.1f} s "
                               f"({b - a:.1f} s still)", a, under.index, fix,
                               [] if fix else ["choose another clip (sve broll --force)", "remove it"],
                               f"freeze:{_where(source)}", more))
        else:
            what = under.label if under is not None else "the presenter"
            found.append(Issue("frozen_picture", f"{what}: the picture does not move {a:.1f}–{b:.1f} s", a,
                               None if under is None else under.index, None,
                               ["look at that moment", "leave it"], f"freeze:{round(a)}"))
    for i in (metrics.get("text_boxes") or {}).get("issues") or []:
        fix = inside_safe_zone(plan, layout, i, cfg) if r["ladder"] else None
        found.append(Issue("text_outside_safe_zone", f"text «{str(i.get('text', ''))[:30]}» at {float(i['start']):.1f} s "
                           f"outside the safe zone ({', '.join(i.get('issues') or [])})", float(i["start"]), None, fix,
                           [] if fix else ["move or shorten it", "leave it"], f"safe:{round(float(i['start']))}"))
    music = music_lower(metrics, cfg) if r["ladder"] else None
    if music is not None:
        found.append(music)
    for e in check.get("errors") or []:
        if r["ladder"] and str(e).startswith("integrated loudness"):
            found.append(Issue("loudness_off", str(e), options=[
                "listen to the listen/ snippets and look at check.json", "render it final (sve make --final)",
                "leave it"], key="loudness"))
            continue
        found.append(Issue("render_error", str(e), options=["look at check.json and the listen/ snippets"],
                           key=f"error:{str(e)[:24]}"))
    return sorted(found, key=lambda x: (x.t is None, x.t or 0.0))


def inside_safe_zone(plan: dict, layout: Layout, box: dict, cfg: dict | None) -> dict | None:
    """The remedy of a plan text outside the safe zone: its `y` moved by what is out (above the top or
    below the bottom), so the whole box is inside. None for a subtitle, a box too tall for the zone or one
    under the buttons on the right (those are decisions)."""
    if str(box.get("kind") or "text") != "text":
        return None
    issues = box.get("issues") or []
    moves = [_EDGE.match(str(x)) for x in issues]
    if not issues or not all(moves) or len({m[1] for m in moves}) > 1:
        return None
    m = moves[0]
    value, limit = float(m[2]), float(m[3])
    delta = (limit - value + SAFE_MARGIN) if m[1] == "top" else -(value - limit + SAFE_MARGIN)
    want = str(box.get("text") or "").strip().lower()
    texts = plan.get("texts") or []
    for k, start, _ in layout.texts:
        tx = texts[k]
        if str(tx.get("text") or "").strip().lower() == want and abs(start - float(box.get("start") or 0)) < 0.35:
            styles = ((cfg or {}).get("texts") or {}).get("styles") or {}
            y = tx.get("y", (styles.get(str(tx.get("style") or "simple")) or {}).get("y"))
            if y is None:
                return None
            return {"text": k, "set": {"y": round(min(max(float(y) + delta, 0.0), 1.0), 3)}}
    return None


def music_lower(metrics: dict, cfg: dict | None) -> Issue | None:
    """The music closer to the voice than `check.music_min_under_voice_lu` while it speaks → lowered by
    what was missing (+MUSIC_MARGIN): `music.under_voice_db` when the music follows the voice
    (`music.relative_to_voice`), else its two volumes. A setting, not the plan (see `apply`)."""
    mv = metrics.get("music_vs_voice") or {}
    if mv.get("speech_lu") is None or cfg is None:
        return None
    limit = float(((metrics.get("thresholds") or {}).get("music_min_under_voice_lu"))
                  or ((cfg.get("check") or {}).get("music_min_under_voice_lu")) or 6.0)
    missing = limit - float(mv["speech_lu"])
    if missing <= 0:
        return None
    m = cfg.get("music") or {}
    down = round(missing + MUSIC_MARGIN, 1)
    if m.get("relative_to_voice"):
        change = {"under_voice_db": round(float(m.get("under_voice_db", 10)) + down, 1)}
    else:
        change = {k: round(float(m.get(k, d)) - down, 1) for k, d in (("volume_with_voice_db", -24.0),
                                                                     ("volume_without_voice_db", -12.0))}
    return Issue("music_covers_voice", f"the music is only {float(mv['speech_lu']):+.1f} LU under the voice while it "
                 f"speaks (at least {limit:g})", mv.get("worst_t"), None, {"settings": {"music": change}},
                 [], "music:voice")


# ── remedies ─────────────────────────────────────────────────────────────────

def apply(plan: dict, issues: list[Issue], tried: set[str], adjust: dict | None = None) -> tuple[list[str], list[Issue]]:
    """Applies, for each finding, the first of its remedies not tried before (changes plan["cutaways"],
    plan["shots"] and plan["texts"] in place; a settings remedy goes into `adjust`, and without `adjust`
    it is left for a person) → (what was done, the findings left for a person)."""
    cutaways = plan.setdefault("cutaways", [])
    done: list[str] = []
    pending: list[Issue] = []
    drops: set[int] = set()
    added: list[dict] = []
    for x in issues:
        step = _next_step(x, tried, adjust is not None)
        if step is None:
            if not x.options:                       # an engine remedy that did not hold
                x.options = ["leave it"] + (["remove it"] if x.cutaway is not None else [])
            pending.append(x)
            continue
        k, fix = step
        if x.cutaway is not None and x.cutaway in drops:
            continue
        tried.update(_tags(x.key, k, fix))
        entry = cutaways[x.cutaway] if x.cutaway is not None else None
        if fix.get("drop"):
            drops.add(int(x.cutaway))
            tried.add(f"removed:{_where(entry)}")
            done.append(f"{x.code}: removed cutaway {x.cutaway} ({_label(entry)})")
        elif fix.get("another_clip"):
            entry["_rejected"] = [*(entry.get("_rejected") or []), str(fix["another_clip"])]
            for key in ("file", "clip_start", "focus_x", "focus_y", "fit", "_match"):
                entry.pop(key, None)
            entry["_fixed"] = sorted({*(entry.get("_fixed") or []), x.code})
            done.append(f"{x.code}: cutaway {x.cutaway} ({_label(entry)}) gets another clip (the next candidate, "
                        f"not {fix['another_clip']})")
        elif fix.get("card"):
            keep = {key: entry[key] for key in CARD_KEEP if key in entry}
            was = sorted({*(entry.get("_was") or []), str(entry.get("template") or entry.get("type") or "graphic")})
            cutaways[x.cutaway] = {"type": "graphic", **fix["card"], **keep, "_auto": entry.get("_auto") or "card",
                                   "_was": was, "_fixed": sorted({*(entry.get("_fixed") or []), x.code})}
            done.append(f"{x.code}: cutaway {x.cutaway} ({_label(entry)}) drawn as a {fix['card'].get('template')} instead")
        elif fix.get("shot") is not None:
            shot = plan["shots"][int(fix["shot"])]
            if fix.get("plain"):
                shot.pop("zoom", None)
                shot["effects"] = [e for e in shot.get("effects") or [] if e.get("type") not in ("push_in", "pull_out")]
                shot["_plain"] = True
                what = "plain framing (no punch-in, no slow zoom)"
            else:
                shot.update(fix["set"])
                what = ", ".join(f"{key}={v!r}" for key, v in fix["set"].items())
            shot["_fixed"] = sorted({*(shot.get("_fixed") or []), x.code})
            done.append(f"{x.code}: shot {fix['shot']} {what}")
        elif fix.get("text") is not None:
            text = plan["texts"][int(fix["text"])]
            text.update(fix.get("set") or {})
            text["_fixed"] = sorted({*(text.get("_fixed") or []), x.code})
            done.append(f"{x.code}: text {fix['text']} «{str(text.get('text') or '')[:24]}» "
                        + ", ".join(f"{key}={v!r}" for key, v in (fix.get("set") or {}).items()))
        elif fix.get("settings"):
            for section, values in fix["settings"].items():
                adjust.setdefault(section, {}).update(values)
            done.append(f"{x.code}: " + ", ".join(f"{section}.{key}={v!r}" for section, values in fix["settings"].items()
                                                  for key, v in values.items()))
        elif fix.get("set"):
            entry.update(fix["set"])
            entry["_fixed"] = sorted({*(entry.get("_fixed") or []), x.code})
            done.append(f"{x.code}: cutaway {x.cutaway} ({_label(entry)}) " +
                        ", ".join(f"{key}={v!r}" for key, v in fix["set"].items()))
        elif fix.get("add"):
            have = {(c.get("at_sentence"), c.get("_auto")) for c in cutaways + added}
            new = [c for c in fix["add"] if (c.get("at_sentence"), c.get("_auto")) not in have
                   and not ({f"short:{_where(c)}", f"left:{_where(c)}", f"removed:{_where(c)}"} & tried)]   # never back once removed
            added += new
            if new:
                done.append(f"{x.code}: {len(new)} cutaway(s) proposed at sentence "
                            + ", ".join(str(c.get("at_sentence")) for c in new))
            else:
                pending.append(x)
    if drops or added:
        kept = [c for k, c in enumerate(cutaways) if k not in drops] + added
        cutaways[:] = sorted(kept, key=lambda c: (c.get("at_sentence") is None, c.get("at_sentence") or 0,
                                                  float(c.get("start") or 0)))
    return done, pending


def _step_name(fix: dict) -> str:
    name = next(iter(fix))
    if name == "another_clip":
        return f"another_clip:{fix['another_clip']}"
    return "plain" if fix.get("plain") else name


def _tags(key: str, k: int, fix: dict) -> set[str]:
    """What marks a remedy as tried: the finding's key for its first remedy (as before the ladder), and
    key|remedy for every remedy (another clip: per clip turned down)."""
    tags = {f"{key}|{_step_name(fix)}"}
    if k == 0 and _step_name(fix) in LEGACY:
        tags.add(key)
    return tags


def _next_step(x: Issue, tried: set[str], settings_ok: bool) -> tuple[int, dict] | None:
    """(position, remedy) of the first remedy of a finding not tried yet; None when none is left."""
    for k, fix in enumerate([f for f in [x.fix, *x.more] if f]):
        if fix.get("settings") and not settings_ok:
            continue
        if _tags(x.key, k, fix) & tried:
            continue
        return k, fix
    return None


def _label(c: dict) -> str:
    return str(c.get("template") or c.get("query") or c.get("file") or c.get("type") or "cutaway")


def _where(c: dict) -> str:
    """Identifies a cutaway across rounds (indices change when one is removed)."""
    return f"{c.get('type', 'graphic')}/{_label(c)}/{c.get('at_sentence', c.get('start'))}"


def _longer(c: dict) -> str:
    if c.get("at_sentence") is not None:
        return f"show it until sentence {int(c.get('until_sentence', c['at_sentence'])) + 1}"
    return "give it more seconds"


def decision_lines(pending: list[dict]) -> list[str]:
    """The decisions left for a person, with their options (also in `sve report`)."""
    if not pending:
        return []
    out = [f"DECISIONS ({len(pending)}): the engine could not settle these"]
    for k, x in enumerate(pending, 1):
        out.append(f"  {k}. {x['message']}")
        if x.get("options"):
            out.append("     → " + " · ".join(x["options"]))
    return out
