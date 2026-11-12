"""The explainer plan: a script said to the camera becomes the video's main track (the best take of
every sentence, in script order) plus the cutaways its cues ask for.

    plan = {"note", "script": "script.md",
            "shots":     [{"source", "start", "end", "transition": "cut", "sentences": [0, 1], "note"}],
            "sentences": [{"n", "text", "source", "start", "end", "score", "takes"} | {"n", "text", "missing": true}],
            "cutaways":  [{"type": "graphic", "template": "bars", …, "at_sentence": 2, "until_sentence": 2}],
            "texts": []}
"""
from __future__ import annotations

from .cutaways import Cutaway, covers
from .graphics.spec import split_list
from .models import PlanError
from .script import CAMERA, GRAPHIC, Script
from .script_align import join_shots, settings
from .timeline import Timeline

SCRIPT_DEFAULTS = {"file": "script.md", "punch_in": 1.08,
                   "close_up": 0.0,          # a third, closer framing in the rotation (1.15; 0 = wide / punch-in only)
                   "reframe_every": 0.0,     # seconds: a longer shot is split between sentences and reframed (0 = off)
                   "drift": 0.0,             # slow zoom each camera shot makes while it plays (0.03 = 3 %; 0 = off)
                   "support": False}         # notes proposed around the presenter (domain/support.py)
TIMED_OVERLAYS = ("bullets", "callouts")     # overlays whose items can appear sentence by sentence
DRIFT_SECONDS = 5.0                          # a shot at least this long drifts the whole `drift`; shorter ones, less
AUTO = "auto"                                # marks the effects the planner adds (replaced when it runs again)
FACE_MIN_RATIO = 0.5        # the face must be found in at least this share of the frames to frame by it
EYES_ABOVE_CENTER = 0.12    # eyes sit this share of the face size above the middle of the face box
EYE_LINE = 1 / 3            # where the eyes go when the picture is cropped (upper third)
BLUR_ASPECT = 1.3           # aspect ratios this different are framed with blurred borders (no crop)
NOTE_CHARS = 70


def script_settings(cfg: dict) -> dict:
    return {**SCRIPT_DEFAULTS, **settings(cfg)}


def build_plan(script: Script, matches: list[dict | None], cfg: dict, script_name: str = "script.md") -> dict:
    """Shots, sentences and cutaways from the script and where each sentence was found (script_align.align)."""
    p = script_settings(cfg)
    texts = [s.text for s in script.sentences]
    shots = []
    for shot in join_shots(matches, float(p["join_gap"]), float(p.get("reframe_every") or 0)):
        said = " ".join(texts[k] for k in shot["sentences"])
        shots.append({"source": shot["source"], "start": shot["start"], "end": shot["end"], "speed": 1,
                      "role": "story", "transition": "cut", "effects": [], "sentences": shot["sentences"],
                      **({"continues": True} if shot.get("continues") else {}),
                      "note": said if len(said) <= NOTE_CHARS else said[:NOTE_CHARS - 1] + "…"})
    if not shots:
        raise PlanError("None of the script's sentences was found in the recording. Check that the script is what "
                        "was said (sve thumbs <project> shows the transcript) and transcription.language.")
    sentences = []
    for s, m in zip(script.sentences, matches):
        entry = {"n": s.index, "text": s.text}
        entry.update({"missing": True} if m is None else m)
        sentences.append(entry)
    cutaways = []
    for cue in script.cues:
        said = script.cue_sentences(cue.index)
        if cue.kind == CAMERA or not said:
            continue
        entry = {"type": cue.kind, **({"template": cue.spec.get("template")} if cue.kind == GRAPHIC else {}),
                 **{k: v for k, v in cue.spec.items() if k != "template"},
                 "at_sentence": said[0], "until_sentence": said[-1]}
        if cue.seconds:
            entry["seconds"] = cue.seconds
        times = _sentence_times(cue.spec, said, matches)
        if times:
            entry["item_times"] = times
        cutaways.append(entry)
    found = sum(m is not None for m in matches)
    return {"note": f"Explainer: {found} of {len(matches)} script sentences found, {len(shots)} shots, "
                    f"{len(cutaways)} cutaways",
            "script": script_name, "shots": shots, "sentences": sentences, "cutaways": cutaways, "texts": []}


def _sentence_times(spec: dict, said: list[int], matches: list[dict | None]) -> list[list] | None:
    """A `bullets` / `callouts` cue with one item per sentence said under it and no `times` of its own:
    each item appears when its sentence starts → [[source, seconds], …] (resolved to the video's time
    when the cutaways are placed)."""
    if spec.get("template") not in TIMED_OVERLAYS or spec.get("times") is not None or len(said) < 2:
        return None
    if len(split_list(spec.get("items"))) != len(said) or any(matches[k] is None for k in said):
        return None
    return [[matches[k]["source"], matches[k]["start"]] for k in said]


def subtitle_analyses(plan: dict, analyses: list[dict], cfg: dict) -> list[dict]:
    """The analyses the subtitles are made from. With script.subtitles: script (the default), the
    words inside every sentence found are the script's own (plan sentences' "words": what was meant,
    with the times it was said) instead of the transcription's; the rest is untouched."""
    sentences = [s for s in plan.get("sentences") or []
                 if isinstance(s, dict) and s.get("words") and isinstance(s.get("source"), int)]
    if not sentences or str(script_settings(cfg).get("subtitles", "script")) != "script":
        return analyses
    out = list(analyses)
    for source in {s["source"] for s in sentences}:
        if source >= len(analyses):
            continue
        mine = [s for s in sentences if s["source"] == source]
        spans = [(float(s["start"]), float(s["end"])) for s in mine]
        kept = [w for w in analyses[source].get("words") or []
                if not any(a <= (w["start"] + w["end"]) / 2 <= b for a, b in spans)]
        written = [{"word": w, "start": a, "end": b, "prob": 1.0} for s in mine for w, a, b in s["words"]]
        out[source] = {**analyses[source], "words": sorted(kept + written, key=lambda w: w["start"])}
    return out


def face_focus(face: dict, src_w: float, src_h: float, W: int, H: int, mode: str = "auto") -> tuple[float, float]:
    """focus_x / focus_y for a shot of a source whose face sits at face["x"], face["y"] (0–1 of the
    picture; "size" = face height as a share of the picture height, "eyes" = height of the eyes).

    A dimension that is cropped to fill the frame is cropped around the face (centered; eyes on the
    upper third). One that is not cropped takes the face's own position, so a zoom grows around it."""
    x, y = float(face["x"]), float(face["y"])
    eyes = float(face.get("eyes", y - EYES_ABOVE_CENTER * float(face.get("size", 0))))
    r_src, r_out = src_w / src_h, W / H
    blur = mode == "blur" or (mode == "auto" and max(r_src, r_out) / min(r_src, r_out) > BLUR_ASPECT)
    if blur:                                        # the whole picture fitted inside the frame
        fit = min(W / src_w, H / src_h)
        fw, fh = src_w * fit, src_h * fit
        return _unit(((W - fw) / 2 + x * fw) / W), _unit(((H - fh) / 2 + y * fh) / H)
    cover = max(W / src_w, H / src_h)
    cw, ch = src_w * cover, src_h * cover
    fx = _unit((x * cw - W / 2) / (cw - W)) if cw - W > 1 else _unit(x)
    fy = _unit((eyes * ch - H * EYE_LINE) / (ch - H)) if ch - H > 1 else _unit(y)
    return fx, fy


def refocus_faces(shots: list[dict], infos: list[dict], analyses: list[dict], old: tuple[int, int],
                  new: tuple[int, int], mode: str = "auto") -> int:
    """Shots framed on the face for a frame of `old` (W, H) are framed again for `new` — the same plan
    rendered with an export profile of another shape. Only the focus frame_shots wrote is replaced: one
    edited by hand stays. → how many shots changed."""
    changed = 0
    for s in shots:
        source = s.get("source")
        if not isinstance(source, int) or not 0 <= source < len(analyses) or "focus_x" not in s or "focus_y" not in s:
            continue
        face = (analyses[source] or {}).get("face")
        if not face or float(face.get("ratio", 0)) < FACE_MIN_RATIO:
            continue
        info = infos[source]
        before = face_focus(face, info["width"], info["height"], *old, mode)
        if abs(float(s["focus_x"]) - before[0]) > 0.002 or abs(float(s["focus_y"]) - before[1]) > 0.002:
            continue
        s["focus_x"], s["focus_y"] = face_focus(face, info["width"], info["height"], *new, mode)
        changed += 1
    return changed


def frame_shots(plan: dict, timeline: Timeline, cutaways: list[Cutaway], cfg: dict, infos: list[dict],
                analyses: list[dict]) -> dict:
    """Frames the camera shots (in place), after the pauses were shortened:

    - framings: at every visible jump cut inside the same source the zoom moves to the next of
      wide (1) → script.punch_in → wide → script.close_up, so the jump reads as a change of shot and
      one camera gives three sizes of shot (without close_up: wide and punch-in alternate). A cut
      hidden under a cutaway and the joins of a shortened pause keep the zoom they had.
    - face: a source whose face was found (analysis "face") gets focus_x / focus_y on it.
    - drift: every camera shot zooms slowly while it plays (script.drift), in when it is wide and out
      when it is punched in, continuous across the joins of its shortened pauses.
    Returns {"punch_ins", "framed"} (counts, for the log)."""
    p = script_settings(cfg)
    punch, close = float(p.get("punch_in") or 0), float(p.get("close_up") or 0)
    sizes = [1.0, punch] if punch > 1 else [1.0]
    if punch > 1 and close > 1:
        sizes += [1.0, close]
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    mode = str((cfg.get("framing") or {}).get("mode", "auto"))
    step, previous, counts = 0, None, {"punch_ins": 0, "framed": 0}
    for clip in timeline:
        s = clip.data
        window = clip.piece.source_window()
        if window is None:
            previous = None
            continue
        source = window[0]
        if previous != source:
            step = 0
        elif not s.get("pause_cut") and len(sizes) > 1 and not covers(cutaways, clip.out_start):
            step = (step + 1) % len(sizes)
            counts["punch_ins"] += sizes[step] > 1
        previous = source
        if sizes[step] > 1:
            s["zoom"] = sizes[step]
        else:
            s.pop("zoom", None)
        face = (analyses[source] or {}).get("face") if source < len(analyses) else None
        if face and float(face.get("ratio", 0)) >= FACE_MIN_RATIO:
            info = infos[source]
            s["focus_x"], s["focus_y"] = face_focus(face, info["width"], info["height"], W, H, mode)
            counts["framed"] += 1
    _drift(timeline, float(script_settings(cfg).get("drift") or 0))
    return counts


def _drift(timeline: Timeline, amount: float) -> None:
    """A slow zoom on every camera shot (in place). A run = a shot and the pieces its shortened pauses
    split it into: the zoom moves over the whole run, so nothing jumps at those joins. A wide run
    pushes in, a punched-in one pulls out (the size changes least at the cuts between them)."""
    runs: list[list] = []
    for clip in timeline:
        s = clip.data
        if clip.piece.source_window() is None:
            continue
        s["effects"] = [e for e in s.get("effects") or [] if not e.get(AUTO)]
        if s.get("pause_cut") and runs and runs[-1][-1].index == clip.index - 1:
            runs[-1].append(clip)
        else:
            runs.append([clip])
    if amount <= 0:
        return
    for run in runs:
        total = sum(c.dur for c in run)
        if total < 1.0:
            continue
        a = amount * min(total / DRIFT_SECONDS, 1.0)
        base = float(run[0].data.get("zoom", 1.0))
        out = base > 1.0
        done = 0.0
        for c in run:
            f0, f1 = done / total, (done + c.dur) / total
            done += c.dur
            start = base + a * ((1 - f1) if out else f0)
            if abs(start - 1.0) > 1e-4:
                c.data["zoom"] = round(start, 4)
            else:
                c.data.pop("zoom", None)
            c.data["effects"].append({"type": "pull_out" if out else "push_in", "amount": round(a * (f1 - f0), 4), AUTO: True})


def _unit(v: float) -> float:
    return round(min(max(v, 0.0), 1.0), 3)
