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

A remedy is applied once: when the same finding comes back after it, it becomes a decision. Every
plan entry a remedy changed keeps the codes in "_fixed" (private: it does not change the render).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import director
from .script import tokens
from .cutaways import MIN_BETWEEN, Cutaway
from .graphics.scene import TEMPLATES

REVIEW_FILE = "review.json"     # <project>/review.json: what the last `sve make` fixed and left
DEFAULTS = {"rounds": 3, "read_cps": 15.0, "graphic_max": 6.0, "camera_slack": 1.5}
ENGINE = ("director", "card", "support", "review")    # "_auto" of the cutaways the engine proposed (it may remove them)
READ_KEYS = ("kicker", "title", "text", "label", "prefix", "value", "unit", "sub", "data", "items", "nodes",
             "name", "specs", "price", "outlet", "date")
FEWEST_WORDS = 2            # a shortened card keeps at least this many words
FEWEST_ITEMS = 2            # …and a list at least this many items
HEARD_SHARE = 0.3           # a word the voice says while it is shown is read along: it counts this share
SLACK = 0.25                # seconds a measure may run over its limit before it counts
FACE_BACK = 1.5             # a graphic cut short leaves the presenter at least this long (domain/director.MIN_FACE)
CLOSING = "the closing sentence stays on camera"


@dataclass
class Issue:
    code: str
    message: str
    t: float | None = None                  # second of the video where it is
    cutaway: int | None = None              # index in plan["cutaways"]
    fix: dict | None = None                 # {"drop": True} | {"set": {...}} | {"add": [cutaways]}
    options: list[str] = field(default_factory=list)       # what a person can do (when there is no fix)
    key: str = ""                           # the same finding in another round has the same key

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

def review_plan(plan: dict, layout: Layout, cfg: dict) -> list[Issue]:
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
            short = shorten(c.spec, length * float(r["read_cps"])) if engine else None
            found.append(Issue("text_too_long_to_read", f"{c.label} at {c.start:.1f} s: {len(words)} characters need "
                               f"{need_read:.1f} s to read, it is shown {length:.1f} s", c.start, c.index,
                               {"set": short} if short else ({"drop": True} if engine else None),
                               [] if engine else ["shorten its text", _longer(source)], f"read:{_where(source)}"))
        limit = float(r["graphic_max"])
        if limit > 0 and length > limit + SLACK:
            seconds = round(max(need, min(limit, length - FACE_BACK)), 2)
            one = engine or (c.sentences is not None and c.sentences[0] == c.sentences[1])
            fix = {"set": {"seconds": seconds}} if one and length - seconds >= MIN_BETWEEN else None
            found.append(Issue("cutaway_too_long", f"{c.label} at {c.start:.1f} s stays {length:.1f} s (most "
                               f"{limit:g} s): the picture rests too long", c.start, c.index, fix,
                               [] if fix else [f"split it (seconds={limit:g} and another cue for the rest)",
                                               "leave it"], f"long:{_where(source)}"))
    found += camera_stretches(plan, layout, cfg)
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


def camera_stretches(plan: dict, layout: Layout, cfg: dict) -> list[Issue]:
    """Stretches where the presenter is alone (no full-frame cutaway) for longer than the director allows."""
    d = director.settings(cfg)
    if not d["auto_broll"] or not layout.spans:
        return []
    slack = float(settings(cfg)["camera_slack"])
    covers = sorted((c.start, c.end) for c in layout.placed if not c.overlay and not c.pip)
    last = layout.spans[max(layout.spans)]
    out, t = [], 0.0
    for a, b in covers + [(layout.total, layout.total)]:
        end = min(a, last[0]) if b >= layout.total - 1e-6 else a   # the closing sentence stays on camera
        allowed = float(d["opening"] if t < 0.05 else d["max_camera"]) + slack
        if end - t > allowed:
            inside = [n for n, (s0, s1) in layout.spans.items() if s0 < end - 0.05 and s1 > t + 0.05]
            fresh = [c for c in director.propose(plan, cfg, overlay_templates()) if c.get("at_sentence") in inside]
            out.append(Issue("camera_too_long", f"the presenter is alone on camera {end - t:.1f} s from {t:.1f} s "
                             f"(most {allowed - slack:g} s)", t, None, {"add": fresh} if fresh else None,
                             [] if fresh else ["add a [graphic] or [broll] cue to that sentence", "leave it"],
                             f"camera:{inside[0] if inside else round(t)}"))
        t = max(t, b)
    return out


def overlay_templates() -> set[str]:
    """Templates drawn over the picture (they do not hide the presenter)."""
    return {name for name, tpl in TEMPLATES.items() if getattr(tpl, "overlay", False)}


def shorten(spec: dict, chars: float) -> dict | None:
    """The text of an engine card cut to about `chars` characters (None when it cannot be shortened
    and still say something): a title or scene keeps its first words, a list its first items, a figure
    drops the line under it, then its label."""
    template = str(spec.get("template") or "")
    if template in ("bars", "diagram", "list") and spec.get("title"):
        return {"title": ""}
    if template in ("title", "scene", "headline") and spec.get("text"):
        words = str(spec["text"]).rstrip("…").split()
        while len(words) > FEWEST_WORDS and len(" ".join(words) + "…") > chars:
            words.pop()
        text = " ".join(words).rstrip(",;:") + ("…" if len(words) < len(str(spec["text"]).rstrip("…").split()) else "")
        rest = len(reading({**spec, "text": ""}))
        return {"text": text} if text != spec["text"] and len(text) + rest <= chars + 8 else None
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


# ── the render ───────────────────────────────────────────────────────────────

def review_render(plan: dict, layout: Layout, check: dict) -> list[Issue]:
    """Findings of `sve check` (check.json) that point at something on screen."""
    metrics = check.get("metrics") or {}
    cutaways = plan.get("cutaways") or []
    found: list[Issue] = []
    for a, b in (metrics.get("video") or {}).get("freeze") or []:
        under = next((c for c in layout.placed if not c.overlay and c.start - 0.1 <= a < c.end), None)
        if under is not None and under.kind == "broll":
            need = layout.minimum.get(under.index, 0.0)
            moving = a - under.start
            source = cutaways[under.index]
            fix = ({"set": {"seconds": round(moving, 2)}} if moving >= need
                   else {"drop": True} if by_engine(source) else None)
            found.append(Issue("frozen_picture", f"the clip {under.label} stops moving at {a:.1f} s "
                               f"({b - a:.1f} s still)", a, under.index, fix,
                               [] if fix else ["choose another clip (sve broll --force)", "remove it"],
                               f"freeze:{_where(source)}"))
        else:
            what = under.label if under is not None else "the presenter"
            found.append(Issue("frozen_picture", f"{what}: the picture does not move {a:.1f}–{b:.1f} s", a,
                               None if under is None else under.index, None,
                               ["look at that moment", "leave it"], f"freeze:{round(a)}"))
    for i in (metrics.get("text_boxes") or {}).get("issues") or []:
        found.append(Issue("text_outside_safe_zone", f"text «{str(i.get('text', ''))[:30]}» at {float(i['start']):.1f} s "
                           f"outside the safe zone ({', '.join(i.get('issues') or [])})", float(i["start"]),
                           options=["move or shorten it", "leave it"], key=f"safe:{round(float(i['start']))}"))
    for e in check.get("errors") or []:
        found.append(Issue("render_error", str(e), options=["look at check.json and the listen/ snippets"],
                           key=f"error:{str(e)[:24]}"))
    return sorted(found, key=lambda x: (x.t is None, x.t or 0.0))


# ── remedies ─────────────────────────────────────────────────────────────────

def apply(plan: dict, issues: list[Issue], tried: set[str]) -> tuple[list[str], list[Issue]]:
    """Applies the remedies that were not tried before (changes plan["cutaways"] in place)
    → (what was done, the findings left for a person)."""
    cutaways = plan.setdefault("cutaways", [])
    done: list[str] = []
    pending: list[Issue] = []
    drops: set[int] = set()
    added: list[dict] = []
    for x in issues:
        if not x.fix or x.key in tried:
            if not x.options:                       # an engine remedy that did not hold
                x.options = ["leave it"] + (["remove it"] if x.cutaway is not None else [])
            pending.append(x)
            continue
        if x.cutaway is not None and x.cutaway in drops:
            continue
        tried.add(x.key)
        if x.fix.get("drop"):
            drops.add(int(x.cutaway))
            done.append(f"{x.code}: removed cutaway {x.cutaway} ({_label(cutaways[x.cutaway])})")
        elif x.fix.get("set"):
            entry = cutaways[x.cutaway]
            entry.update(x.fix["set"])
            entry["_fixed"] = sorted({*(entry.get("_fixed") or []), x.code})
            done.append(f"{x.code}: cutaway {x.cutaway} ({_label(entry)}) " +
                        ", ".join(f"{k}={v!r}" for k, v in x.fix["set"].items()))
        elif x.fix.get("add"):
            have = {(c.get("at_sentence"), c.get("_auto")) for c in cutaways + added}
            new = [c for c in x.fix["add"] if (c.get("at_sentence"), c.get("_auto")) not in have
                   and not ({f"short:{_where(c)}", f"left:{_where(c)}"} & tried)]   # never back once removed
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
