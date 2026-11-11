"""Vector paths as plain data: no drawing library here, the canvas adapter turns them into real paths.

A path is a tuple of commands: ("M", x, y), ("L", x, y), ("C", x1, y1, x2, y2, x, y), ("Z",).
`native` holds a path made by the adapter (text outlines); `dx`/`dy` move either kind.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

Point = tuple[float, float]
KAPPA = 0.5522847498          # cubic Bézier handle length for a quarter circle


@dataclass(frozen=True)
class Path:
    cmds: tuple = ()
    dx: float = 0.0
    dy: float = 0.0
    native: object = None

    def moved(self, dx: float, dy: float) -> Path:
        return replace(self, dx=self.dx + dx, dy=self.dy + dy)

    def bounds(self) -> tuple[float, float, float, float] | None:
        """(x0, y0, x1, y1) of the control points; None for adapter-made paths."""
        xs, ys = [], []
        for c in self.cmds:
            xs += c[1::2]
            ys += c[2::2]
        if not xs:
            return None
        return min(xs) + self.dx, min(ys) + self.dy, max(xs) + self.dx, max(ys) + self.dy


def poly(pts: list[Point], closed: bool = True) -> Path:
    cmds = [("M", *pts[0]), *(("L", *p) for p in pts[1:])]
    if closed:
        cmds.append(("Z",))
    return Path(tuple(cmds))


def rect(x: float, y: float, w: float, h: float) -> Path:
    return poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])


def round_rect(x: float, y: float, w: float, h: float, r: float) -> Path:
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        return rect(x, y, w, h)
    k = r * (1 - KAPPA)
    x1, y1 = x + w, y + h
    return Path((
        ("M", x + r, y), ("L", x1 - r, y), ("C", x1 - k, y, x1, y + k, x1, y + r),
        ("L", x1, y1 - r), ("C", x1, y1 - k, x1 - k, y1, x1 - r, y1),
        ("L", x + r, y1), ("C", x + k, y1, x, y1 - k, x, y1 - r),
        ("L", x, y + r), ("C", x, y + k, x + k, y, x + r, y), ("Z",)))


def oval(cx: float, cy: float, rx: float, ry: float) -> Path:
    kx, ky = rx * KAPPA, ry * KAPPA
    return Path((
        ("M", cx + rx, cy),
        ("C", cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry),
        ("C", cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy),
        ("C", cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry),
        ("C", cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy), ("Z",)))


def circle(cx: float, cy: float, r: float) -> Path:
    return oval(cx, cy, r, r)


def _handles(pts: list[Point], i: int, closed: bool, tension: float) -> tuple[Point, Point, Point]:
    n = len(pts)
    p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
    p1, p2 = pts[i], pts[(i + 1) % n]
    p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
    t = tension / 3 * 2
    c1 = (p1[0] + (p2[0] - p0[0]) * t / 2, p1[1] + (p2[1] - p0[1]) * t / 2)
    c2 = (p2[0] - (p3[0] - p1[0]) * t / 2, p2[1] - (p3[1] - p1[1]) * t / 2)
    return c1, c2, p2


def smooth(pts: list[Point], closed: bool = True, tension: float = 0.5) -> Path:
    """A curve through the points (Catmull-Rom converted to cubic Béziers)."""
    if len(pts) < 2:
        return Path()
    cmds = [("M", *pts[0])]
    for i in (range(len(pts)) if closed else range(len(pts) - 1)):
        c1, c2, p2 = _handles(pts, i, closed, tension)
        cmds.append(("C", *c1, *c2, *p2))
    if closed:
        cmds.append(("Z",))
    return Path(tuple(cmds))


def smooth_points(pts: list[Point], per: int = 14, tension: float = 0.5) -> list[Point]:
    """The open smooth curve through `pts`, sampled as a dense polyline (to draw part of it or to
    move something along it)."""
    if len(pts) < 2:
        return list(pts)
    out = [pts[0]]
    for i in range(len(pts) - 1):
        p1 = pts[i]
        c1, c2, p2 = _handles(pts, i, False, tension)
        for k in range(1, per + 1):
            s = k / per
            a, b, c, d = (1 - s) ** 3, 3 * (1 - s) ** 2 * s, 3 * (1 - s) * s ** 2, s ** 3
            out.append((a * p1[0] + b * c1[0] + c * c2[0] + d * p2[0], a * p1[1] + b * c1[1] + c * c2[1] + d * p2[1]))
    return out


def length(pts: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def cut(pts: list[Point], p: float) -> list[Point]:
    """The first fraction `p` (0-1) of a polyline, by length."""
    if p >= 1 or len(pts) < 2:
        return list(pts)
    if p <= 0:
        return [pts[0]]
    left, out = length(pts) * p, [pts[0]]
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if seg >= left:
            s = left / seg if seg else 0
            out.append((a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s))
            return out
        out.append(b)
        left -= seg
    return out


def point_at(pts: list[Point], p: float) -> tuple[Point, float]:
    """Point at fraction `p` of a polyline and the direction there in degrees."""
    part = cut(pts, max(0.0, min(p, 1.0)))
    end = part[-1]
    a, b = (part[-2], part[-1]) if len(part) > 1 and part[-2] != part[-1] else (pts[0], pts[1] if len(pts) > 1 else pts[0])
    return end, math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def dashes(pts: list[Point], dash: float, gap: float, offset: float = 0.0) -> list[list[Point]]:
    """A polyline cut into dashes of `dash` length separated by `gap`, shifted by `offset` along it
    (animating the offset from 0 to dash + gap makes them march one step)."""
    period = dash + gap
    if len(pts) < 2 or period <= 0:
        return []
    out: list[list[Point]] = []
    pos = -(offset % period)                 # distance along the line where the next dash starts
    walked = 0.0
    cur: list[Point] | None = None
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if seg <= 0:
            continue

        def at(d: float) -> Point:
            s = (d - walked) / seg
            return a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s

        end = walked + seg
        while True:
            if cur is None:
                start = max(pos, 0.0)
                if pos + dash <= 0:          # a dash wholly before the line starts
                    pos += period
                    continue
                if start >= end:
                    break
                cur = [at(max(start, walked))]
            stop = pos + dash
            if stop <= end:
                cur.append(at(stop))
                out.append(cur)
                cur = None
                pos += period
            else:
                cur.append(b)
                break
        walked = end
    if cur and len(cur) > 1:
        out.append(cur)
    return [d for d in out if len(d) > 1]
