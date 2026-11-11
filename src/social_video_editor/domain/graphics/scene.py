"""A graphic as a scene: where it is drawn (Stage), what it draws at each instant (Scene) and the
registries of templates and reusable elements (packs add to both)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from ..models import PlanError
from . import motion
from .canvas import Canvas, fake_measure
from .spec import seconds

# safe zone as fractions (x0, y0, x1, y1): vertical keeps clear of the apps' buttons on the right and
# leaves the band below for the narration box (kit.caption) and the apps' own texts; landscape leaves
# room for subtitles
SAFE_VERTICAL = (0.06, 0.08, 0.88, 0.77)
SAFE_LANDSCAPE = (0.08, 0.10, 0.92, 0.82)
# a graphic is laid out at the largest of these scales at which it fits in the safe zone
SCALES = (3.0, 2.8, 2.6, 2.4, 2.2, 2.0, 1.8, 1.65, 1.5, 1.35, 1.2, 1.1, 1.0, 0.9, 0.8)


@dataclass
class Stage:
    """The frame a graphic is laid out for. Sizes in templates are written for a 1080-wide (or
    1080-tall) frame and multiplied by `u`."""

    W: int
    H: int
    theme: dict
    measure: Callable[[str, str, float], float] = fake_measure
    language: str = "es"
    image_size: Callable[[str], tuple[int, int] | None] = lambda file: (1000, 1000)     # None = unreadable
    u: float = field(init=False)
    vertical: bool = field(init=False)
    safe: tuple[float, float, float, float] = field(init=False)

    def __post_init__(self):
        self.u = min(self.W, self.H) / 1080
        self.vertical = self.H > self.W
        f = self.theme.get("safe", {}).get("vertical" if self.vertical else "landscape") or (
            SAFE_VERTICAL if self.vertical else SAFE_LANDSCAPE)
        self.safe = (f[0] * self.W, f[1] * self.H, f[2] * self.W, f[3] * self.H)

    def px(self, v: float) -> float:
        return v * self.u

    @property
    def safe_w(self) -> float:
        return self.safe[2] - self.safe[0]

    @property
    def safe_h(self) -> float:
        return self.safe[3] - self.safe[1]

    @property
    def center(self) -> tuple[float, float]:
        return (self.safe[0] + self.safe[2]) / 2, (self.safe[1] + self.safe[3]) / 2


class Scene(ABC):
    """One graphic: laid out once (layout) and drawn for any instant (draw)."""

    name = ""
    overlay = False            # True: drawn over the video on a transparent background
    default_dur = 3.5

    scales: tuple[float, ...] | None = None      # sizes tried, largest first (None: from the theme)

    def __init__(self, spec: dict, stage: Stage):
        self.spec, self.st = spec, stage
        self.dur = seconds(spec, "dur", self.default_dur)
        self.exit_at: float | None = None  # when a closing animation starts (None: it just stays)
        sc = stage.theme.get("scale") or {}
        top = float(sc.get("max_vertical" if stage.vertical else "max", 1.8))
        self.fill = float(sc.get("fill", 0.94))
        for k in self.scales or [s for s in SCALES if s <= top] or [1.0]:
            self.k = k
            self.block = (0.0, 0.0)        # (width, height) the content needs: layout() sets it
            self.settle = self.dur         # when the entrances are over (layout() lowers it)
            self.layout()
            if self.fits():
                break
        self.settle = min(self.settle, self.dur)

    def z(self, v: float) -> float:
        """A size written for a 1080 frame, in pixels of this stage at the scale the graphic got."""
        return v * self.st.u * self.k

    def fits(self) -> bool:
        """Whether the content laid out at the current scale stays inside the safe zone."""
        w, h = self.block
        return w <= self.st.safe_w + 1 and h <= self.st.safe_h * self.fill + 1

    @abstractmethod
    def layout(self) -> None:
        """Reads the spec and places everything at scale self.k (sizes through self.z), and sets
        self.block. Called once per scale tried. Raises PlanError for a bad spec."""

    @abstractmethod
    def draw(self, cv: Canvas, t: float) -> None:
        """Draws the graphic at `t` seconds (the backdrop is already there)."""

    def paint(self, cv: Canvas, t: float) -> None:
        from . import kit

        if not self.overlay:
            kit.scenery(cv, self.st, t, seed=sum(map(ord, self.name)))
        self.draw(cv, t)
        if self.spec.get("caption"):               # what is being said while the graphic is on screen
            kit.caption(cv, self.st, str(self.spec["caption"]), t)

    def frame_state(self, t: float) -> tuple[tuple, float]:
        """(key, time to draw) for the frame at `t`: frames with the same key look the same, so the
        renderer draws each key once. Objects move on twos when the theme says so. After the
        entrances the picture keeps living (ambient motion, stroke wobble) in a loop of
        motion.LOOP_TICKS steps, so a settled graphic costs LOOP_TICKS drawings at most."""
        m = self.st.theme["motion"]
        tick = motion.ticks(t)
        td = tick / motion.ON_TWOS_FPS if m.get("on_twos") else t
        first = motion.ticks(self.settle - 1e-9) + 1          # first whole tick at or after settle
        if (self.exit_at is not None and td >= self.exit_at) or tick < first:
            return ("moving", round(td, 4)), td
        alive = m.get("boil") or m.get("ambient", True)
        phase = (tick - first) % motion.LOOP_TICKS if alive else 0
        return ("settled", phase), (first + phase) / motion.ON_TWOS_FPS

    def sounds(self) -> list[tuple[str, float]]:
        """(sound effect, seconds from the start of the graphic)."""
        return [("whoosh", 0.0)]

    def lines(self) -> list[str]:
        """The words on screen once everything has appeared (to check there is time to read them)."""
        return []


TEMPLATES: dict[str, type[Scene]] = {}
ELEMENTS: dict[str, Callable] = {}


def template(name: str):
    """Registers a Scene class as a template (a pack can replace a built-in one)."""
    def register(cls: type[Scene]) -> type[Scene]:
        cls.name = name
        TEMPLATES[name] = cls
        return cls
    return register


def element(name: str):
    """Registers a reusable drawing, `fn(cv, st, x, y, t, **options)` (packs replace "marker" etc.)."""
    def register(fn: Callable) -> Callable:
        ELEMENTS[name] = fn
        return fn
    return register


def make_scene(spec: dict, stage: Stage) -> Scene:
    name = str(spec.get("template") or "").lower()
    if name not in TEMPLATES:
        raise PlanError(f"Unknown graphic template '{name}'. Options: {', '.join(sorted(TEMPLATES))}")
    try:
        return TEMPLATES[name](spec, stage)
    except ValueError as e:                  # e.g. an unknown color name
        raise PlanError(f"Graphic '{name}': {e}") from None
