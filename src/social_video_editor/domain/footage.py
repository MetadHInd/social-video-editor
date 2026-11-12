"""Third-party footage (b-roll): clips fetched for the `broll` cues from sources the user allows —
free stock libraries, a commons with open licenses, and the official channels of the brands the user
lists — each one written down with where it comes from.

Pure rules, no network and no files: which candidates are acceptable and which is best, which piece
of a clip is used, what the record (footage/credits.json) and the credits say, and how long a clip
of each kind may stay on screen. No number of seconds makes third-party footage safe; short pieces,
allowed sources and a record of every file lower the risk and leave a trail.

A candidate (what a source returns, and what is ranked here):

    {"source": "pexels", "kind": "stock", "id": "123", "title": "", "url": "https://…/file.mp4",
     "page": "https://…", "author": "Ana", "license": "Pexels License", "license_code": "pexels",
     "width": 1080, "height": 1920, "duration": 14.0}                (+ "brand", "channel_id": official)
"""
from __future__ import annotations

import html
import re
import unicodedata

from .models import PlanError

STOCK, OFFICIAL = "stock", "official"
FOLDER = "footage"                      # inside the project folder
RECORD = "credits.json"                 # inside FOLDER
CREDITS_FILE = "credits.txt"            # inside a version folder, for the video's description
CREDIT_AUTO = "credit"                  # "_auto" mark of the credit overlays `sve broll` adds
OPEN_LICENSES = ("cc0", "pd", "cc-by", "cc-by-sa")
DEFAULTS = {
    "sources": ["official", "pexels", "pixabay", "wikimedia"],     # tried in this order
    "candidates": 2,                    # clips downloaded per cue; the one that shows the query best is kept
    "search_limit": 12,                 # results asked of each source
    "max_seconds": {"official": 5.0, "stock": 8.0},
    "max_share": {"official": 0.15},    # share of the video an official channel's footage may take
    "credit": True,                     # a caption with the source the first time each one is on screen
    "credit_seconds": 3.0,
    "min_height": 720,                  # short side of the clip, pixels
    "max_megabytes": 250,
    "licenses": list(OPEN_LICENSES),    # accepted from a commons (stock libraries have their own license)
    "framing": "crop",                  # crop | blur | auto: how a clip of another shape fills the frame
    "text": False,                      # true: clips that show printed text (titles, captions) may be used
    "fallback_card": True,              # no clip passes: an illustrated title card with what is said, instead
    "min_match": 0.25,                  # vision score under which a clip does not show the query (≈0.22 unrelated, ≈0.28 right)
    "preview_minutes": 12,              # a video longer than this is not looked through (any source)
    "brands": [],                       # [{"name", "channel_id", "aliases": [...]}] (a pack's brands.yaml adds to it)
}
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[^\W_]+")
_CHANNEL = re.compile(r"^UC[\w-]{22}$")


class FootageError(Exception):
    """A source could not answer (no key, no network, an unexpected response). The next source is tried."""


def settings(cfg: dict | None) -> dict:
    """The `broll` section with defaults (max_seconds / max_share merged key by key)."""
    given = (cfg or {}).get("broll") or {}
    out = {**DEFAULTS, **{k: v for k, v in given.items() if v is not None}}
    for k in ("max_seconds", "max_share"):
        out[k] = {**DEFAULTS[k], **(given.get(k) or {})}
    return out


def plain(text) -> str:
    """Text without HTML (a commons gives the author as a link)."""
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", str(text or "")))).strip()


def words(text) -> list[str]:
    flat = unicodedata.normalize("NFKD", str(text or "").lower())
    return _WORD.findall("".join(c for c in flat if not unicodedata.combining(c)))


def license_code(text) -> str | None:
    """A license as a commons names it ("CC BY-SA 4.0", "cc-by-3.0-de", "Public domain", "CC0") →
    cc0 | pd | cc-by | cc-by-sa; None for anything else (non-commercial, no-derivatives, GFDL alone,
    fair use…)."""
    t = [w for w in words(str(text or "").replace("-", " ")) if not w.isdigit()]
    if not t:
        return None
    if t[0] == "cc0" or t[:2] == ["cc", "zero"]:
        return "cc0"
    if t[0] in ("pd", "pdm") or t[:2] == ["public", "domain"]:
        return "pd"
    if t[:2] == ["cc", "by"]:
        rest = t[2:]
        if any(x in ("nc", "nd") for x in rest):
            return None
        return "cc-by-sa" if rest[:1] == ["sa"] else "cc-by"
    return None


def brands(listed) -> list[dict]:
    """The brands whose official channel may be used: [{"name", "channel_id", "aliases"}], checked."""
    out = []
    for b in listed or []:
        if not isinstance(b, dict) or not b.get("name") or not _CHANNEL.match(str(b.get("channel_id") or "")):
            raise PlanError(f"broll.brands: every brand needs a name and its channel_id (UC… , 24 characters): {b!r}")
        aliases = b.get("aliases") or []
        out.append({"name": str(b["name"]), "channel_id": str(b["channel_id"]),
                    "aliases": [str(a) for a in ([aliases] if isinstance(aliases, str) else aliases)]})
    return out


def find_brand(text, listed: list[dict]) -> dict | None:
    """The listed brand a query names (its name or an alias as whole words: "pixel" is not in "pixels")."""
    said = words(text)
    best = None
    for b in listed:
        for name in (b["name"], *b.get("aliases", [])):
            w = words(name)
            if w and any(said[i:i + len(w)] == w for i in range(len(said) - len(w) + 1)):
                if best is None or len(w) > best[0]:
                    best = (len(w), b)
    return best[1] if best else None


def rejection(c: dict, s: dict, brand: dict | None = None) -> str | None:
    """Why a candidate cannot be used (None: it can)."""
    if not c.get("url"):
        return "no file to download"
    if c.get("kind") == OFFICIAL and (brand is None or c.get("channel_id") != brand["channel_id"]):
        return "not from a listed official channel"
    if float(c.get("duration") or 0) > float(s["preview_minutes"]) * 60:
        return f"longer than {s['preview_minutes']:g} min"
    if c.get("kind") == OFFICIAL:
        return None
    code = c.get("license_code")
    if code in OPEN_LICENSES and code not in s["licenses"]:
        return f"license {c.get('license') or code} is not accepted (broll.licenses)"
    if code is None:
        return f"license not accepted: {c.get('license') or 'unknown'}"
    if c.get("width") and c.get("height") and min(int(c["width"]), int(c["height"])) < int(s["min_height"]):
        return f"too small ({c['width']}×{c['height']})"
    return None


def fit(c: dict, W: int, H: int, seconds: float) -> float:
    """How well a candidate suits a frame of W×H for `seconds`: same orientation, enough pixels to
    cover the frame without enlarging, long enough."""
    w, h = float(c.get("width") or 0), float(c.get("height") or 0)
    out = 0.0
    if w and h:
        same = (w >= h) == (W >= H) if abs(w - h) > 1 else None
        out += 2.0 if same else 1.0 if same is None else 0.0
        out += min(1.0 / max(W / w, H / h), 1.0)
    duration = float(c.get("duration") or 0)
    out += min(duration / seconds, 1.0) if seconds > 0 and duration > 0 else 0.5
    return out


def rank(candidates: list[dict], W: int, H: int, seconds: float, s: dict,
         brand: dict | None = None) -> tuple[list[dict], list[tuple[dict, str]]]:
    """(usable candidates, best first; [(candidate, why not)]). A source's own order (its relevance to
    the query) breaks ties."""
    kept, rejected = [], []
    for k, c in enumerate(candidates):
        why = rejection(c, s, brand)
        if why:
            rejected.append((c, why))
        else:
            kept.append((fit(c, W, H, seconds) - 0.04 * k, -k, c))
    return [c for _, _, c in sorted(kept, key=lambda x: (x[0], x[1]), reverse=True)], rejected


def limit(kind: str, s: dict) -> float:
    return float(s["max_seconds"].get(kind) or s["max_seconds"][STOCK])


def middle_window(duration: float, seconds: float, kind: str = STOCK) -> float:
    """Where a piece of `seconds` starts when nobody looked at the clip: centered in a stock clip;
    a fifth into an official video (after its opening titles)."""
    free = max(float(duration) - seconds, 0.0)
    return round(free * (0.2 if kind == OFFICIAL else 0.5), 3)


TEXT_MAX = 0.01             # share of the picture covered by lines of text above which a frame "shows text"
TEXT_PENALTY = 0.3          # score taken from a frame full of text (a vision score is ≈0.2–0.35): text is avoided
CENTER_PRIOR = 0.02         # score taken from a piece at the far end of the clip (ties go to the middle)
KEEP_WHOLE = 0.8            # a clip that keeps at least this share of its width (height) when it fills the frame
#                             is framed in the middle; a narrower crop is placed where the clip shows best
CROPS = 5                   # places tried across the clip for that crop


def crop_spans(src_w: float, src_h: float, W: int, H: int, n: int = CROPS) -> list[tuple[float, float, tuple]]:
    """Where a clip of src_w×src_h can be cropped to fill a W×H frame → [(focus_x, focus_y, (x0, y0, x1, y1))],
    the boxes as shares of the clip. One centered crop when the clip keeps most of itself (KEEP_WHOLE);
    else n crops from one side to the other (focus 0 … 1)."""
    if not (src_w and src_h and W and H):
        return [(0.5, 0.5, (0.0, 0.0, 1.0, 1.0))]
    cover = max(W / src_w, H / src_h)
    keep_w, keep_h = min(W / (src_w * cover), 1.0), min(H / (src_h * cover), 1.0)
    if min(keep_w, keep_h) >= KEEP_WHOLE or n < 2:
        x0, y0 = (1 - keep_w) / 2, (1 - keep_h) / 2
        return [(0.5, 0.5, (x0, y0, x0 + keep_w, y0 + keep_h))]
    out = []
    for i in range(n):
        f = i / (n - 1)
        if keep_w < keep_h:                                   # a wide clip in a tall frame: slide sideways
            x0 = (1 - keep_w) * f
            out.append((round(f, 3), 0.5, (x0, 0.0, x0 + keep_w, 1.0)))
        else:
            y0 = (1 - keep_h) * f
            out.append((0.5, round(f, 3), (0.0, y0, 1.0, y0 + keep_h)))
    return out


def text_in(boxes, crop: tuple) -> float:
    """Share of a crop (x0, y0, x1, y1) covered by lines of text (boxes x, y, w, h; all shares of the clip)."""
    x0, y0, x1, y1 = crop
    area = max((x1 - x0) * (y1 - y0), 1e-9)
    covered = 0.0
    for x, y, w, h in boxes or []:
        ix, iy = max(min(x + w, x1) - max(x, x0), 0.0), max(min(y + h, y1) - max(y, y0), 0.0)
        covered += ix * iy
    return min(covered / area, 1.0)


def nearest(times, t: float) -> int:
    """Index of the sample time closest to t."""
    return min(range(len(times)), key=lambda i: abs(float(times[i]) - t)) if len(times) else 0


def choose_piece(times, scores, texts, seconds: float, duration: float, kind: str = STOCK) -> tuple[float, float | None, float]:
    """The piece of a clip to show → (start, its vision score or None, share of its frames that show text).

    scores: the vision score of each sampled frame (None without the model); texts: how much of each frame
    is text (in the least texty crop). Frames with text are avoided (TEXT_PENALTY); ties go to the middle."""
    if not len(times):
        return middle_window(duration, seconds, kind), None, 0.0
    mid = middle_window(duration, seconds, kind)
    span = max(float(duration), 1.0)
    base = [float(x) for x in scores] if scores is not None else [0.0] * len(times)
    adjusted = [b - TEXT_PENALTY * min(float(tx) / TEXT_MAX, 1.0) - CENTER_PRIOR * abs(float(t) - mid) / span
                for b, tx, t in zip(base, texts, times)]
    start, _ = best_window(times, adjusted, seconds, duration)
    inside = [i for i, t in enumerate(times) if start - 1e-6 <= float(t) < start + max(seconds, 1e-3)] or [nearest(times, start)]
    shown = sum(1 for i in inside if float(texts[i]) > TEXT_MAX) / len(inside)
    score = sum(base[i] for i in inside) / len(inside) if scores is not None else None
    return start, score, shown


def pick_crop(sims, texts) -> int:
    """Which crop to use: the one that shows the query best (sims[crop][frame], or None without the model)
    and has the least text (texts[crop][frame]); ties go to the middle."""
    n = len(texts)
    if n <= 1:
        return 0

    def value(c: int) -> float:
        seen = sum(sims[c]) / len(sims[c]) if sims is not None and len(sims[c]) else 0.0
        text = sum(min(float(x) / TEXT_MAX, 1.0) for x in texts[c]) / max(len(texts[c]), 1)
        return seen - TEXT_PENALTY * text - CENTER_PRIOR * abs(c / (n - 1) - 0.5)
    return max(range(n), key=value)


SUBJECT_MASS = 0.85         # share of the picture's detail that counts as "the subject"
SUBJECT_MARGIN = 0.05       # room kept on each side of the subject (share of the clip)
SCENE = 0.8                 # detail spread over more than this share of the clip: a scene, not one subject
POOLED = 160                # a frame is looked at this many blocks wide when measuring where its detail is
EDGE_PERCENTILE = 85        # only the strongest edges count (textures and grain have many weak ones)
BACKGROUND_DISTANCE = 40.0  # colour distance (0–441) from the border's colour that counts as "not background"


def detail_profile(frame, axis: int) -> list[float]:
    """Where the subject of a picture (H×W×3 or H×W array) is along its columns (axis=1) or rows
    (axis=0), as shares that add up to 1: strong edges plus what differs from the colour of the picture's
    border (the background of a product shot: a white wall, a cloth, a blurred room)."""
    import numpy as np

    f = np.asarray(frame, np.float32)
    if f.ndim == 2:
        f = f[..., None]
    k = max(int(round(max(f.shape[:2]) / POOLED)), 1)                  # averaged into blocks: grain and noise vanish
    h, w = f.shape[0] // k * k, f.shape[1] // k * k
    if h < 3 * k or w < 3 * k:
        return []
    f = f[:h, :w].reshape(h // k, k, w // k, k, f.shape[2]).mean(axis=(1, 3))
    gray = f.mean(axis=2)
    e = np.abs(np.diff(gray, axis=1))[:-1, :] + np.abs(np.diff(gray, axis=0))[:, :-1]
    e = np.where(e > max(float(np.percentile(e, EDGE_PERCENTILE)), 3.0), e, 0.0)   # only the strongest edges
    border = np.concatenate([f[0], f[-1], f[:, 0], f[:, -1]])
    far = np.linalg.norm(f - np.median(border, axis=0), axis=2)[:-1, :-1]
    far = np.where(far > max(float(np.percentile(far, 50)), BACKGROUND_DISTANCE), far, 0.0)
    parts = [x / x.sum() for x in (e, far) if x.sum() > 0]
    if not parts:
        return []
    m = sum(parts)
    prof = m.sum(axis=0 if axis == 1 else 1)
    return [float(x) / float(prof.sum()) for x in prof]


def subject_span(profiles: list[list[float]], mass: float = SUBJECT_MASS) -> tuple[float, float] | None:
    """(start, end) as shares of the clip where most of the detail of a few frames is (the subject),
    or None when the frames hold no detail."""
    import numpy as np

    rows = [np.asarray(p, float) for p in profiles if len(p)]
    if not rows:
        return None
    n = min(len(r) for r in rows)
    prof = np.mean([np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(r)), r) for r in rows], axis=0)
    lifted = prof - float(np.median(prof))                   # what stands above the background's even level
    prof = lifted.clip(min=0) if lifted.max() > 0 else prof
    prof = prof / max(float(prof.sum()), 1e-12)
    cum = np.cumsum(prof)
    lo = float(np.searchsorted(cum, (1 - mass) / 2)) / n
    hi = float(np.searchsorted(cum, 1 - (1 - mass) / 2) + 1) / n
    return max(lo, 0.0), min(hi, 1.0)


def framing(src_w: float, src_h: float, W: int, H: int, span: tuple[float, float] | None) -> dict:
    """How a clip of another shape fills the frame, from where its subject is (subject_span along the
    side that would be cropped) → {"mode": "whole" | "crop" | "fit" | "scene", "focus": share,
    "fit": share of the clip shown across the frame (fit only)}.

    whole: it keeps most of itself, centered. crop: the subject fits in the crop: centered on it. fit:
    the subject is wider than the crop would be: the clip is shown large, zoomed to the subject (with a
    margin), over a blurred copy of itself, so nothing is cut in half. scene: detail everywhere (a
    landscape, a room): cropped where it shows best (pick_crop)."""
    spans = crop_spans(src_w, src_h, W, H)
    if len(spans) == 1:
        return {"mode": "whole", "focus": 0.5}
    x0, y0, x1, y1 = spans[0][2]
    wide = spans[0][0] != spans[-1][0]                       # the crop slides sideways (a wide clip in a tall frame)
    keep = (x1 - x0) if wide else (y1 - y0)
    if span is None:
        return {"mode": "scene", "focus": 0.5}
    a, b = span
    need = min(b - a + 2 * SUBJECT_MARGIN, 1.0)
    center = (a + b) / 2
    if need > SCENE:
        return {"mode": "scene", "focus": 0.5}
    if need <= keep:
        return {"mode": "crop", "focus": round(min(max((center - keep / 2) / (1 - keep), 0.0), 1.0), 3)}
    show = min(max(need, keep), 1.0)                         # the subject whole, as large as that allows
    focus = 0.5 if show >= 1.0 else min(max((center - show / 2) / (1 - show), 0.0), 1.0)
    return {"mode": "fit", "focus": round(focus, 3), "fit": round(show, 3)}


def best_window(times, scores, seconds: float, duration: float) -> tuple[float, float]:
    """(start, mean score) of the `seconds` of a clip that show the query best, from the score of
    its sampled frames. A clip no longer than that is used from its start."""
    times, scores = [float(t) for t in times], [float(x) for x in scores]
    if not times:
        return 0.0, 0.0
    if duration <= seconds:
        return 0.0, sum(scores) / len(scores)
    best = (0.0, float("-inf"))
    for i, t0 in enumerate(times):
        if t0 + seconds > duration + 1e-6:
            break
        inside = [x for t, x in zip(times[i:], scores[i:]) if t < t0 + seconds]
        mean = sum(inside) / len(inside)
        if mean > best[1] + 1e-9:
            best = (t0, mean)
    if best[1] == float("-inf"):
        return 0.0, sum(scores) / len(scores)
    return round(best[0], 3), best[1]


def file_name(c: dict, ext: str = ".mp4") -> str:
    """footage/<source>_<id>_<a few words>.mp4 — readable, and the same clip always gets the same name."""
    slug = "_".join(words(c.get("title") or c.get("query") or "")[:4])[:40]
    ident = re.sub(r"[^\w-]", "", str(c.get("id") or "clip"))[:40]
    return "_".join(x for x in (str(c.get("source") or "clip"), ident, slug) if x) + ext


def record(c: dict, file: str, sha256: str | None, query: str, downloaded_at: str,
           piece: tuple[float, float] | None = None) -> dict:
    """The entry of footage/credits.json for a downloaded clip."""
    out = {"file": file, "sha256": sha256, "source": c.get("source"), "kind": c.get("kind", STOCK),
           "url": c.get("page") or c.get("url"), "author": c.get("author") or "", "license": c.get("license") or "",
           "downloaded_at": downloaded_at, "query": query, "used": True}
    for k in ("title", "license_url", "author_url", "brand", "channel_id"):
        if c.get(k):
            out[k] = c[k]
    if piece:
        out["piece"] = [round(float(piece[0]), 3), round(float(piece[1]), 3)]     # of the original video
    return out


LABEL_CHARS = 26                        # an author's name on the caption (the full name stays in the record)
_AUTHOR_END = re.compile(r"\s*(?:\(|/|;|,|\s[-–—]\s|\b(?:obra derivada|derivative work|based on|after)\b)", re.I)


def short_author(author) -> str:
    """The author's name for the caption: up to the first note, list or parenthesis, at most
    LABEL_CHARS ("engineerguy (Bill Hammack, …)" → "engineerguy")."""
    name = _AUTHOR_END.split(plain(author), maxsplit=1)[0].strip(" .·-")
    if len(name) > LABEL_CHARS:
        name = name[:LABEL_CHARS].rsplit(" ", 1)[0].rstrip(" .,·-") + "…"
    return name


def credit_label(rec: dict) -> str:
    """What the caption on screen says: the brand of an official clip; author and library otherwise."""
    if rec.get("kind") == OFFICIAL:
        return str(rec.get("brand") or short_author(rec.get("author")) or "Official channel")
    library = SOURCE_NAMES.get(str(rec.get("source")), str(rec.get("source") or "").title())
    author = short_author(rec.get("author"))
    return f"{author} · {library}" if author else library


def credit_line(rec: dict) -> str:
    """One line for the video's description (credits.txt)."""
    if rec.get("kind") == OFFICIAL:
        who = str(rec.get("brand") or rec.get("author") or "official channel")
        return f"Video: {who} (official channel) — {rec.get('url')}"
    library = SOURCE_NAMES.get(str(rec.get("source")), str(rec.get("source") or "").title())
    author = str(rec.get("author") or "").strip() or "unknown author"
    licensed = f", {rec['license']}" if rec.get("license") else ""
    return f"Video: {author} / {library}{licensed} — {rec.get('url')}"


def credits_text(records: list[dict]) -> str:
    """credits.txt: one line per clip used, no clip twice."""
    lines, seen = [], set()
    for r in records:
        line = credit_line(r)
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def issues(used: list[dict], total: float, s: dict) -> list[tuple[str, str]]:
    """Findings about the third-party footage of a rendered video → [(code, message)].

    used: [{"file", "kind" (stock | official | None), "start", "end", "recorded": bool}] — the b-roll
    cutaways as they were shown. Codes: third_party_too_long, third_party_share, footage_without_record."""
    out = []
    by_kind: dict[str, float] = {}
    for u in used:
        length = float(u["end"]) - float(u["start"])
        kind = u.get("kind") or STOCK
        by_kind[kind] = by_kind.get(kind, 0.0) + length
        if not u.get("recorded"):
            out.append(("footage_without_record",
                        f"footage without a record: {u.get('file')} ({u['start']:.1f}–{u['end']:.1f} s) is not in "
                        f"{FOLDER}/{RECORD} or the file changed since (where does it come from?)"))
        if length > limit(kind, s) + 0.05:
            out.append(("third_party_too_long",
                        f"third-party footage too long: {u.get('file')} ({kind}) stays {length:.1f} s "
                        f"({u['start']:.1f}–{u['end']:.1f} s), limit {limit(kind, s):g} s (broll.max_seconds)"))
    for kind, share in s["max_share"].items():
        if total > 0 and share is not None and by_kind.get(kind, 0.0) / total > float(share) + 1e-6:
            out.append(("third_party_share",
                        f"third-party footage share: {kind} clips take {by_kind[kind] / total:.0%} of the video, "
                        f"limit {float(share):.0%} (broll.max_share)"))
    return out


SOURCE_NAMES = {"pexels": "Pexels", "pixabay": "Pixabay", "wikimedia": "Wikimedia Commons", "official": "Official channel"}
