"""The video as a tree of pieces (Composite pattern).

    Sequence (whole video)                       composite
      ├── VideoShot   a cut of a source clip      leaf
      ├── Group "star product"                    composite (optional: consecutive shots with
      │     ├── VideoShot (fast)                    the same "group" label in the plan)
      │     ├── VideoShot (slow, continuous)
      │     └── PolaroidShot (from_shot = …)      leaf drawn from another shot's last frame
      ├── CollageShot (from_shots = […])          leaf drawn from several shots' frames
      └── PhotoShot (source = a still photo)      leaf: a photo with a gentle Ken Burns motion

Every piece answers duration(), children() and walk(). Leaves (shots) also know how to
fit a beat-aligned duration, which other shots they are drawn from (dependencies), which
sound effects they trigger and how to describe themselves; rendering dispatches through
`accept(visitor)` instead of branching on the shot type.

A shot wraps the plan's dict (no copy): changes made by the beat alignment land in the plan.
Plan shot keys (English): source, start, end, speed, role, transition, effects, beats, continuous,
search, type (video | polaroid | collage | photo | graphic), template, from_shot, from_shots, background_from, text, tilt,
columns, subdivision, zoom, focus_x, focus_y, volume_db, mute, freeze, color, note, group, dur,
duration, motion, motion_amount, framing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Protocol

from .effects import freeze_seconds
from .models import PlanError
from .photos import ALL_MOTIONS, PHOTO_SECONDS, default_motion
from .timebase import DEFAULT_FPS, frames, mmss
from .transitions import entry_overlap

DRAWN_SECONDS_PER_BEAT = 0.6     # drawn shot without "dur": 0.6 s per beat (2 beats by default)
APPEAR_MARGIN = 0.15             # collage photos must start popping at least this long before the end


class PieceVisitor(Protocol):
    """Something that does a different job for each kind of shot (e.g. the renderer)."""

    def visit_video(self, shot: VideoShot): ...
    def visit_polaroid(self, shot: PolaroidShot): ...
    def visit_collage(self, shot: CollageShot): ...
    def visit_photo(self, shot: PhotoShot): ...
    def visit_graphic(self, shot: GraphicShot): ...


class Piece(ABC):
    kind = "piece"

    def children(self) -> list[Piece]:
        return []

    def walk(self) -> Iterator[Piece]:
        """This piece and all its descendants, depth first, in story order."""
        yield self
        for c in self.children():
            yield from c.walk()

    def shots(self) -> list[Shot]:
        """The leaves, in story order."""
        return [p for p in self.walk() if p.is_leaf]

    @property
    def is_leaf(self) -> bool:
        return False

    @abstractmethod
    def duration(self, fps: int = DEFAULT_FPS) -> float:
        """Content length in seconds (transition overlaps are applied by the timeline)."""

    def dependencies(self) -> set[int]:
        """Indices of the shots this piece is drawn from."""
        out: set[int] = set()
        for c in self.children():
            out |= c.dependencies()
        return out


# ── leaves ───────────────────────────────────────────────────────────────────

class Shot(Piece):
    """A leaf: one entry of the plan's "shots" list."""

    def __init__(self, data: dict, index: int):
        self.data, self.index = data, index

    @property
    def is_leaf(self) -> bool:
        return True

    @property
    def transition(self) -> str:
        return self.data.get("transition", "cut")

    @property
    def role(self) -> str:
        return self.data.get("role", "")

    @property
    def source(self) -> int | None:
        """Index of the source clip it cuts from (None for drawn shots)."""
        return None

    @abstractmethod
    def base_duration(self) -> float:
        """Natural duration used to count beats (before frame rounding, without frozen frames)."""

    def natural_duration(self) -> float:
        return self.base_duration()

    def duration(self, fps: int = DEFAULT_FPS) -> float:
        return frames(self.natural_duration(), fps)

    @abstractmethod
    def fit_to_duration(self, dur: float, prev: Shot | None, source_durations: list[float]) -> None:
        """Make the shot last exactly `dur` seconds on screen (beat alignment)."""

    @abstractmethod
    def shift_end(self, delta: float, cfg: dict, source_durations: list[float]) -> bool:
        """Lengthen/shorten the shot by `delta` output seconds; False if not possible."""

    def source_window(self) -> tuple[int, float, float, float] | None:
        """(source, start, end, speed) if the shot plays a piece of a source clip."""
        return None

    def sound_events(self, out_start: float, dur: float, base_db: float, cfg: dict) -> list[tuple[str, float, float]]:
        """Sound effects the shot triggers: (name, second in the video, gain dB)."""
        return []

    def summary(self) -> dict:
        return {"type": self.kind, "source": None, "from": None, "to": None}

    def describe(self) -> str:
        return f"{self.kind:<28}"

    @abstractmethod
    def accept(self, visitor: PieceVisitor):
        ...


class VideoShot(Shot):
    """A cut of a source clip (start/end in source seconds), played at `speed`."""

    kind = "video"

    @property
    def source(self) -> int:
        return int(self.data["source"])

    @property
    def speed(self) -> float:
        return float(self.data.get("speed", 1.0))

    def base_duration(self) -> float:
        return (self.data["end"] - self.data["start"]) / self.speed

    def natural_duration(self) -> float:
        return self.base_duration() + freeze_seconds(self.data)

    def fit_to_duration(self, dur, prev, source_durations):
        d = self.data
        v = self.speed
        length = (dur - freeze_seconds(d)) * v
        if d.get("continuous") and prev is not None and prev.source == self.source:
            d["start"] = prev.data["end"]          # continues exactly where the previous shot ended (ramps)
        maximum = source_durations[self.source]
        d["end"] = round(d["start"] + length, 3)
        if d["end"] > maximum:                    # the source is too short: slide the window back
            d["end"] = round(maximum - 0.02, 3)
            d["start"] = round(max(d["end"] - length, 0), 3)

    def shift_end(self, delta, cfg, source_durations):
        d = self.data
        v = self.speed
        new_end = d["end"] + delta * v
        if new_end - d["start"] < cfg["pacing"]["shot_min"] * v or new_end > source_durations[self.source]:
            return False
        d["end"] = round(new_end, 3)
        return True

    def source_window(self):
        return self.source, self.data["start"], self.data["end"], self.speed

    def summary(self) -> dict:
        return {"type": self.kind, "source": self.source, "from": mmss(self.data["start"]),
                "to": mmss(self.data["end"])}

    def describe(self) -> str:
        return (f"source {self.source} {mmss(self.data['start'])}→{mmss(self.data['end'])} "
                f"x{self.data.get('speed', 1)}")

    def accept(self, visitor):
        return visitor.visit_video(self)


class DrawnShot(Shot):
    """A shot drawn frame by frame from other shots (it has its own "dur")."""

    def base_duration(self) -> float:
        return float(self.data.get("dur", DRAWN_SECONDS_PER_BEAT * float(self.data.get("beats", 2))))

    def fit_to_duration(self, dur, prev, source_durations):
        self.data["dur"] = round(dur, 4)

    def shift_end(self, delta, cfg, source_durations):
        new = self.base_duration() + delta
        if new < cfg["pacing"]["shot_min"]:
            return False
        self.data["dur"] = round(new, 4)
        return True

    def _ref(self, key: str) -> int:
        if key not in self.data:
            raise PlanError(f"Shot {self.index} ({self.kind}) needs '{key}'")
        return int(self.data[key])


class PolaroidShot(DrawnShot):
    """Freezes the last frame of `from_shot` inside an instant photo that drops in with a flash."""

    kind = "polaroid"

    @property
    def from_shot(self) -> int:
        return self._ref("from_shot")

    def dependencies(self) -> set[int]:
        return {self.from_shot}

    def sound_events(self, out_start, dur, base_db, cfg):
        return [("shutter", out_start, base_db + 3)]

    def accept(self, visitor):
        return visitor.visit_polaroid(self)


class CollageShot(DrawnShot):
    """A grid with one frame of each shot in `from_shots`, popping in on the beat."""

    kind = "collage"

    @property
    def from_shots(self) -> list[int]:
        if not self.data.get("from_shots"):
            raise PlanError(f"Shot {self.index} (collage) needs a non-empty 'from_shots' list")
        return [int(k) for k in self.data["from_shots"]]

    @property
    def background_from(self) -> int:
        return int(self.data.get("background_from", self.from_shots[-1]))

    def dependencies(self) -> set[int]:
        return set(self.from_shots) | {self.background_from}

    def appearance_times(self, dur: float, default_subdivision: int = 2) -> list[float]:
        return appearance_times({**self.data, "dur": dur}, len(self.from_shots), default_subdivision)

    def sound_events(self, out_start, dur, base_db, cfg):
        sub = int(cfg.get("collage", {}).get("subdivision", 2))
        return [("pop", out_start + t, base_db - 4) for t in self.appearance_times(dur, sub)]

    def accept(self, visitor):
        return visitor.visit_collage(self)


def appearance_times(shot: dict, n: int, subdivision: int = 2) -> list[float]:
    """When each collage photo appears: on the beats split into `subdivision` parts
    (1 = one per beat, 2 = every half beat, 4 = every quarter beat: faster).

    Every photo appears before the end of the shot (at least APPEAR_MARGIN s before it): if the
    beat grid has no room for all of them, they are spread evenly over the first half.
    """
    dur = float(shot["dur"])
    sub = int(shot.get("subdivision", subdivision))
    beats = [g for g in shot.get("_beats", []) if g < dur]
    if len(beats) >= 2:
        T = beats[1] - beats[0]
        start = beats[0] - T * int(beats[0] / T)          # includes the beat at the very start if it fits
        grid = [start + k * T / sub for k in range(int(dur / (T / sub)) + 1)]
        grid = [g for g in grid if 0.05 <= g <= dur - APPEAR_MARGIN]
        if len(grid) >= n:
            return grid[:n]
    last = max(dur - APPEAR_MARGIN, 0.0)
    return [min(0.1 + k * (dur * 0.5) / max(n, 1), last) for k in range(n)]


class PhotoShot(Shot):
    """A still photo on screen for "duration" seconds with a gentle Ken Burns "motion".

    "source" is a source index, a source id or a path; "motion" is zoom_in | zoom_out | pan_left |
    pan_right | none (default: alternating by position). Beat alignment changes "duration".
    """

    kind = "photo"

    @property
    def ref(self):
        """What "source" says (index, id or path): the renderer resolves it to a file."""
        if self.data.get("source") in (None, ""):
            raise PlanError(f"Shot {self.index} (photo) needs 'source' (a source index or a photo path)")
        return self.data["source"]

    @property
    def motion(self) -> str:
        m = self.data.get("motion") or default_motion(self.index)
        if m not in ALL_MOTIONS:
            raise PlanError(f"Shot {self.index} (photo): unknown motion '{m}'. Options: {', '.join(ALL_MOTIONS)}")
        return m

    def base_duration(self) -> float:
        return float(self.data.get("duration", self.data.get("dur", PHOTO_SECONDS)))

    def fit_to_duration(self, dur, prev, source_durations):
        self.data["duration"] = round(dur, 4)

    def shift_end(self, delta, cfg, source_durations):
        new = self.base_duration() + delta
        if new < cfg["pacing"]["shot_min"]:
            return False
        self.data["duration"] = round(new, 4)
        return True

    def summary(self) -> dict:
        return {"type": self.kind, "source": str(self.data.get("source")), "from": None, "to": None}

    def describe(self) -> str:
        name = str(self.data.get("source", "?")).replace("\\", "/").rsplit("/", 1)[-1]
        return f"photo {name[:18]:<18} {self.data.get('motion') or default_motion(self.index):<9}"

    def accept(self, visitor):
        return visitor.visit_photo(self)


class GraphicShot(DrawnShot):
    """An animated graphic drawn from data (domain/graphics): bars, a big number, a diagram…

    Plan keys: "template" plus the template's own keys, and "dur" (seconds; each template has a default)."""

    kind = "graphic"
    DEFAULT_SECONDS = 3.5

    @property
    def template(self) -> str:
        if not self.data.get("template"):
            raise PlanError(f"Shot {self.index} (graphic) needs 'template'")
        return str(self.data["template"])

    def base_duration(self) -> float:
        return float(self.data.get("dur") or self.DEFAULT_SECONDS)

    def spec(self) -> dict:
        """What the graphic shows: the shot without the keys that only place it in the video."""
        return {k: v for k, v in self.data.items() if k not in GRAPHIC_PLACEMENT_KEYS and not k.startswith("_")}

    def sound_events(self, out_start, dur, base_db, cfg):
        return [(name, out_start + at, base_db - 2) for name, at in self.data.get("_sounds", []) if at < dur]

    def summary(self) -> dict:
        return {**super().summary(), "template": self.data.get("template")}

    def describe(self) -> str:
        return f"graphic {self.data.get('template', '?'):<20}"

    def accept(self, visitor):
        return visitor.visit_graphic(self)


GRAPHIC_PLACEMENT_KEYS = ("type", "transition", "role", "beats", "group", "note", "score", "peak", "voice")
SHOT_TYPES: dict[str, type[Shot]] = {"video": VideoShot, "polaroid": PolaroidShot, "collage": CollageShot,
                                     "photo": PhotoShot, "graphic": GraphicShot}


def make_shot(data: dict, index: int) -> Shot:
    kind = data.get("type") or "video"
    if kind not in SHOT_TYPES:
        raise PlanError(f"Unknown shot type '{kind}' (shot {index}). Options: {', '.join(SHOT_TYPES)}")
    return SHOT_TYPES[kind](data, index)


# ── composites ───────────────────────────────────────────────────────────────

def chain_duration(shots: list, fps: int = DEFAULT_FPS, cfg: dict | None = None) -> float:
    """Length of consecutive shots played one after another, minus the overlaps of their transitions."""
    total, prev = 0.0, None
    for s in shots:
        dur = s.duration(fps)
        total += dur - entry_overlap(s.transition, dur, prev, cfg or {}, fps)
        prev = dur
    return round(total, 4)


class Group(Piece):
    """Consecutive shots that belong together (e.g. a star product: fast + slow + polaroid)."""

    kind = "group"

    def __init__(self, label: str, items: list[Piece]):
        self.label, self.items = label, list(items)

    def children(self) -> list[Piece]:
        return self.items

    def duration(self, fps: int = DEFAULT_FPS, cfg: dict | None = None) -> float:
        """From the start of its first shot to the end of its last one in the video: the overlaps of the
        transitions BETWEEN its shots are subtracted (as compute_timeline does; cfg gives the
        transition durations, None = defaults). The entry transition of the first shot is not."""
        return chain_duration(self.shots(), fps, cfg)


class Sequence(Piece):
    """The whole video: shots and groups in story order."""

    kind = "sequence"

    def __init__(self, items: list[Piece]):
        self.items = list(items)

    @classmethod
    def from_plan(cls, plan: dict) -> Sequence:
        """Builds the tree from plan["shots"]; consecutive shots with the same "group" form a Group."""
        items: list[Piece] = []
        current: Group | None = None
        for i, data in enumerate(plan.get("shots", [])):
            shot = make_shot(data, i)
            label = data.get("group")
            if label is None:
                current = None
                items.append(shot)
            elif current is not None and current.label == label:
                current.items.append(shot)
            else:
                current = Group(label, [shot])
                items.append(current)
        return cls(items)

    def children(self) -> list[Piece]:
        return self.items

    def duration(self, fps: int = DEFAULT_FPS, cfg: dict | None = None) -> float:
        """From the start of its first shot to the end of its last one in the video: the overlaps of the
        transitions BETWEEN its shots are subtracted (as compute_timeline does; cfg gives the
        transition durations, None = defaults). The entry transition of the first shot is not."""
        return chain_duration(self.shots(), fps, cfg)

    def render_order(self) -> list[int]:
        """Shot indices in an order where every shot comes after the shots it is drawn from.

        Shots without dependencies first (story order), then those drawn from them, and so on
        (render_waves flattened). Raises PlanError for references to missing shots or circular references.
        """
        return [i for wave in self.render_waves() for i in wave]

    def render_waves(self) -> list[list[int]]:
        """Shot indices grouped by dependency depth, story order inside each group.

        Wave 0 holds the shots that need no other shot (video, photo), wave k the shots drawn only from
        shots of earlier waves: the shots of one wave never depend on each other, so a renderer may
        encode them concurrently. Raises PlanError like render_order.
        """
        depth = self._depths()
        waves: list[list[int]] = [[] for _ in range(max(depth.values(), default=-1) + 1)]
        for i in range(len(depth)):
            waves[depth[i]].append(i)
        return waves

    def _depths(self) -> dict[int, int]:
        """Dependency depth of every shot (0 = no dependencies); validates the references."""
        shots = self.shots()
        n = len(shots)
        for s in shots:
            for d in sorted(s.dependencies()):
                if not 0 <= d < n:
                    raise PlanError(f"Shot {s.index} ({s.kind}) references shot {d}, "
                                    f"but the plan has {n} shots (0–{n - 1})")
        depth: dict[int, int] = {}
        visiting: set[int] = set()

        def visit(i: int, path: list[int]) -> int:
            if i in depth:
                return depth[i]
            if i in visiting:
                cycle = path[path.index(i):] + [i]
                raise PlanError("Circular reference between shots: " + " → ".join(map(str, cycle)))
            visiting.add(i)
            d = 0
            for dep in sorted(shots[i].dependencies()):
                d = max(d, visit(dep, path + [i]) + 1)
            visiting.discard(i)
            depth[i] = d
            return d

        for i in range(n):
            visit(i, [])
        return depth
