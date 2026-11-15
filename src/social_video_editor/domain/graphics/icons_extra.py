"""More illustrations for a tech explainer (registered into icons.ICONS when icons.py is imported):
the network at rush hour (signal, building, crowd, clock, night, speed, download, a video loading,
water that comes out of a shower or a tap) and the everyday vocabulary of tech (cable, satellite,
keyboard, shield, key, bug, folder, file, photo, camera, microphone, speaker, message, bell,
charger, trash, search, settings, money, calendar, map pin, rocket, idea, warning).

Same rules as icons.py: a 100 × 100 box centered on the origin, the silhouette first (Pen.outline),
then the fills with small details, and a little life that loops (Pen.phase / Pen.on).
"""
from __future__ import annotations

import math

from .canvas import saved
from .icons import Pen, _arc, icon
from .paths import Path, circle, oval, poly, rect, round_rect, smooth

TAU = 2 * math.pi


def _star(x: float, y: float, r: float) -> Path:
    w = r * 0.28
    return poly([(x, y - r), (x + w, y - w), (x + r, y), (x + w, y + w), (x, y + r), (x - w, y + w), (x - r, y),
                 (x - w, y - w)])


def _twinkle(pen: Pen, x: float, y: float, r: float, offset: float) -> None:
    k = 0.55 + 0.45 * math.sin(TAU * pen.phase(offset)) ** 2 if pen.alive else 1.0
    star = _star(x, y, r * k)
    pen.stroke(star, pen.ink, pen.line * 1.1)
    pen.fill(star, pen.paper)


def _line(pen: Pen, x0: float, y0: float, x1: float, y1: float, color: str | None = None, width: float = 2.6,
          alpha: float = 1.0) -> None:
    pen.stroke(poly([(x0, y0), (x1, y1)], closed=False), color, width, alpha)


def _gear_path(cx: float, cy: float, r_out: float, r_in: float, teeth: int, turn: float) -> Path:
    pts = []
    for i in range(teeth):
        a0 = math.radians(turn) + TAU * i / teeth
        step = TAU / teeth
        for f, r in ((0.0, r_in), (0.12, r_out), (0.42, r_out), (0.54, r_in)):
            a = a0 + f * step
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return poly(pts)


def _crescent(R: float, ox: float, oy: float, r: float, n: int = 28) -> Path:
    """A crescent: the disc of radius R at the origin minus the disc of radius r at (ox, oy)."""
    d = math.hypot(ox, oy)
    ux, uy = ox / d, oy / d
    a = (R * R - r * r + d * d) / (2 * d)
    h = math.sqrt(max(R * R - a * a, 0.0))
    g = math.atan2(h, a)
    base = math.atan2(uy, ux)
    outer = [(R * math.cos(base + g + (TAU - 2 * g) * i / n), R * math.sin(base + g + (TAU - 2 * g) * i / n))
             for i in range(n + 1)]
    p_end, p_start = outer[-1], outer[0]
    s0 = math.atan2(p_end[1] - oy, p_end[0] - ox)
    s1 = math.atan2(p_start[1] - oy, p_start[0] - ox)
    sweep = (s1 - s0) % TAU                     # the way round the small circle that stays inside the big one
    mid = s0 + sweep / 2
    if math.hypot(ox + r * math.cos(mid), oy + r * math.sin(mid)) > R:
        sweep -= TAU
    inner = [(ox + r * math.cos(s0 + sweep * i / n), oy + r * math.sin(s0 + sweep * i / n)) for i in range(1, n)]
    return poly(outer + inner)


# ── the network at rush hour ─────────────────────────────────────────────────
@icon("signal", words=("senal", "senales", "signal", "cobertura", "coverage", "rayitas", "reception", "recepcion"))
def signal(pen: Pen) -> None:
    heights = (20, 36, 52, 68)
    bars = [round_rect(-36 + i * 19, 34 - h, 13, h, 3) for i, h in enumerate(heights)]
    base = round_rect(-44, 34, 88, 9, 4.5)
    mast = poly([(-38, -14), (-44, 2), (-32, 2)])
    pen.outline(base, *bars, mast)
    pen.fill(base, pen.muted)
    for i in range(7):                                                       # ticks on the base
        pen.fill(rect(-38 + i * 12.5, 37.5, 5, 2), pen.ink, 0.35)
    pen.fill(mast, pen.dark)
    pen.part(circle(-38, -16, 3.6), pen.color)
    lit = 4 if pen.active > 0.2 else (1 + int(pen.phase() * 4)) if pen.alive else 3
    for i, (bar, h) in enumerate(zip(bars, heights)):
        x = -36 + i * 19
        if i < lit:
            pen.fill(bar, pen.color)
            pen.shade(rect(x, 34 - min(h, 14), 13, min(h, 14)), 0.35)
            pen.fill(rect(x + 2.5, 38 - h, 2.4, h - 10), pen.paper, 0.55)    # a shine
        else:
            pen.fill(bar, pen.dark)
            pen.fill(rect(x + 2.5, 38 - h, 2.4, h - 10), pen.paper, 0.15)
        pen.flash(bar)
    for k, rad in enumerate((8, 14)):                                        # the antenna sends
        if pen.on(0.1 * k, 0.6):
            pen.stroke(_arc(-38, -16, rad, 200, 340, 8), pen.color, 2.6)


@icon("building", words=("edificio", "edificios", "building", "apartamento", "departamento", "apartment", "condominio",
                         "oficina", "office", "bloque de pisos", "torre de departamentos"))
def building(pen: Pen) -> None:
    body = rect(-30, -32, 60, 76)
    top = rect(-34, -38, 68, 7)
    tank = round_rect(10, -54, 16, 12, 3)
    pen.cable(poly([(-20, -38), (-20, -56)], closed=False), 2.6)
    pen.outline(body, top, tank)
    pen.part(circle(-20, -57, 3), pen.alert if pen.on(0.0, 0.5) else pen.muted)
    for x in (13, 23):                                                      # the water tank on its legs
        pen.stroke(poly([(x, -42), (x, -38)], closed=False), pen.ink, 2.2)
    pen.fill(tank, pen.muted)
    pen.fill(rect(10, -50, 16, 2), pen.ink, 0.35)
    pen.fill(body, pen.paper)
    pen.fill(top, pen.dark)
    pen.shade(rect(16, -31, 14, 75), 0.35)
    lit = {(0, 1), (2, 0), (1, 2), (2, 3), (0, 3), (1, 4)}                   # windows with people at home
    for r in range(5):
        for c in range(3):
            x, y = -24 + c * 17, -25 + r * 11.5
            win = rect(x, y, 12, 8)
            on = (c, r) in lit and pen.on(0.17 * (c + 2 * r), 0.85)
            pen.part(win, pen.color if on else pen.dark)
            pen.stroke(poly([(x + 6, y), (x + 6, y + 8)], closed=False), pen.ink, 1.1, 0.6)
            if on:
                pen.fill(rect(x + 1.5, y + 1.5, 3, 5), pen.paper, 0.6)
    pen.part(rect(-7, 32, 14, 12), pen.dark)                                 # the door and its awning
    pen.part(poly([(-11, 32), (11, 32), (8, 27), (-8, 27)]), pen.color)
    pen.fill(circle(4, 38, 1.2), pen.metal)
    pen.part(rect(-40, 43, 80, 4), pen.muted)


@icon("shower", words=("ducha", "duchas", "regadera", "shower", "showers", "banarse", "duchar"))
def shower(pen: Pen) -> None:
    pipe = smooth([(-44, -40), (-14, -40), (2, -36), (8, -26)], closed=False, tension=0.5)
    pen.cable(pipe, 6, pen.metal)
    plate = round_rect(-48, -48, 8, 18, 3)
    head = poly([(-6, -26), (22, -26), (32, -10), (-16, -10)])
    face = round_rect(-18, -11, 52, 6, 3)
    pen.outline(plate, head, face)
    pen.fill(plate, pen.muted)
    pen.fill(head, pen.metal)
    pen.shade(poly([(8, -26), (22, -26), (32, -10), (8, -10)]), 0.35)
    pen.fill(face, pen.dark)
    for i in range(6):
        pen.fill(circle(-12 + i * 8, -8, 1.3), pen.paper, 0.7)
    pen.part(round_rect(-31, -46, 8, 12, 2.5), pen.color)                    # the valve handle
    for col in range(6):                                                     # the water falls, drop after drop
        x = -12 + col * 8 + (col % 2) * 1.5
        for k in range(2):
            f = (pen.phase(0.5 * k + 0.23 * col) if pen.alive else 0.25 + 0.5 * k)
            y = -2 + f * 42
            drop = smooth([(x, y - 5), (x + 2.8, y + 1), (x, y + 3.6), (x - 2.8, y + 1)])
            pen.stroke(drop, pen.ink, pen.line * 0.9)
            pen.fill(drop, pen.color)
    pen.fill(oval(10, 44, 30, 3.2), pen.color, 0.45)
    pen.stroke(oval(10, 44, 30, 3.2), pen.ink, 1.2, 0.5)


@icon("tap", words=("grifo", "grifos", "canilla", "llave de agua", "faucet", "tap", "agua", "water", "gota", "gotas",
                    "tuberia", "tuberias", "caneria", "pipe", "pipes"))
def tap(pen: Pen) -> None:
    spout = smooth([(-4, -16), (16, -16), (24, -8), (24, 4)], closed=False, tension=0.5)
    pen.cable(spout, 11, pen.metal)
    pen.fill(round_rect(19, 2, 10, 5, 2), pen.dark)
    plate = round_rect(-48, -32, 10, 30, 3)
    body = round_rect(-40, -24, 40, 16, 6)
    stem = rect(-24, -34, 7, 10)
    lever = round_rect(-38, -42, 36, 8, 4)
    basin = poly([(-14, 28), (46, 28), (40, 44), (-8, 44)])
    pen.outline(plate, body, stem, lever, basin)
    pen.fill(plate, pen.muted)
    pen.fill(stem, pen.dark)
    pen.fill(lever, pen.color)
    pen.fill(rect(-34, -40, 22, 2), pen.paper, 0.5)
    pen.fill(body, pen.metal)
    pen.shade(rect(-40, -15, 40, 7), 0.35)
    pen.fill(rect(-34, -21, 26, 2.2), pen.paper, 0.6)
    pen.fill(basin, pen.paper)
    pen.shade(poly([(-12, 36), (43, 36), (40, 44), (-8, 44)]), 0.3)
    pen.fill(oval(16, 30, 26, 2.6), pen.color, 0.7)                           # the water in the basin
    p = pen.phase() if pen.alive else 0.3
    if p < 0.5:                                                              # a drop grows at the tip…
        r = 1.5 + 3 * (p / 0.5)
        drop = smooth([(24, 6), (24 + r, 8 + r), (24, 8 + 2 * r), (24 - r, 8 + r)])
    else:                                                                    # …and falls
        y = 12 + (p - 0.5) / 0.5 * 16
        drop = smooth([(24, y - 6), (28, y + 1), (24, y + 4.5), (20, y + 1)])
        if p > 0.85:
            pen.stroke(oval(24, 30, 4 + (p - 0.85) * 60, 1.6), pen.paper, 1.6, 0.8)
    pen.stroke(drop, pen.ink, pen.line * 0.9)
    pen.fill(drop, pen.color)


@icon("clock", words=("reloj", "relojes", "clock", "clocks", "hora", "horas", "horario", "hour", "hours", "minuto",
                      "minutos", "minute", "minutes", "tiempo", "time", "alarma", "alarm", "despertador", "hora pico",
                      "rush hour", "cronometro", "timer", "temporizador"))
def clock(pen: Pen) -> None:
    ring = math.sin(TAU * 3 * pen.phase()) * 4 * max(0.0, 1 - pen.phase() * 3) if pen.alive else 0.0
    with saved(pen.cv):
        pen.cv.rotate(ring, 0, 4)
        bells = [circle(-24, -30, 11), circle(24, -30, 11)]
        legs = [poly([(-20, 34), (-28, 46)], closed=False), poly([(20, 34), (28, 46)], closed=False)]
        for leg in legs:
            pen.cable(leg, 4.5)
        pen.cable(poly([(0, -36), (0, -46)], closed=False), 3)
        pen.outline(*bells)
        for b in bells:
            pen.fill(b, pen.color)
        pen.shade(circle(-20, -27, 7), 0.3)
        pen.shade(circle(28, -27, 7), 0.3)
        pen.part(circle(0, -47, 3.5), pen.metal)
        body = circle(0, 4, 36)
        pen.outline(body)
        pen.fill(body, pen.color)
        face = circle(0, 4, 28.5)
        pen.part(face, pen.paper)
        pen.shade(smooth([(6, 31), (22, 22), (28, 6), (16, 22)]), 0.3)
        for k in range(12):
            a = math.radians(k * 30)
            r0 = 22 if k % 3 else 19
            _line(pen, 25 * math.cos(a), 4 + 25 * math.sin(a), r0 * math.cos(a), 4 + r0 * math.sin(a), pen.ink,
                  2.6 if k % 3 == 0 else 1.6)
        m = math.radians(360 * (pen.phase() if pen.alive else 0.1) - 90)
        hr = math.radians(360 * (pen.phase() if pen.alive else 0.1) / 12 + 120 - 90)
        _line(pen, 0, 4, 12 * math.cos(hr), 4 + 12 * math.sin(hr), pen.ink, 4.2)
        _line(pen, 0, 4, 19 * math.cos(m), 4 + 19 * math.sin(m), pen.alert, 3)
        pen.fill(circle(0, 4, 3.6), pen.ink)
        pen.flash(face)
        pen.stroke(_arc(0, 4, 32, 200, 250), pen.paper, 2.6, 0.8)
    if ring and abs(ring) > 1:                                               # it rings
        for side in (-1, 1):
            for k in range(2):
                pen.stroke(_arc(side * 24, -30, 17 + k * 6, -60 if side > 0 else 200, -10 if side > 0 else 250, 5),
                           pen.paper, 2.4, 0.8)


@icon("moon", words=("luna", "noche", "noches", "nocturno", "nocturna", "madrugada", "medianoche", "moon", "night",
                     "nights", "midnight", "tonight", "de noche"))
def moon(pen: Pen) -> None:
    for x, y, r, o in ((30, -30, 7, 0.0), (-36, -34, 5, 0.33), (34, 22, 5.5, 0.66)):
        _twinkle(pen, x, y, r, o)
    shape = _crescent(34, 15, -12, 28)
    pen.outline(shape)
    pen.fill(shape, pen.color)
    pen.shade(poly([(-34, 4), (-10, 30), (12, 32), (30, 18), (6, 40), (-30, 24)]), 0.35)
    for x, y, r in ((-21, 8, 4.5), (-11, 22, 3.2), (-25, -10, 2.6), (2, 27, 2.4)):
        pen.part(circle(x, y, r), pen.paper, 0.45)
    pen.stroke(smooth([(-12, -6), (-7, -2), (-2, -6)], closed=False), pen.ink, 2.2)        # a sleeping eye
    pen.fill(circle(-17, 1, 2.6), pen.alert, 0.35)
    pen.flash(shape)
    drift = 6 * math.sin(TAU * pen.phase()) if pen.alive else 0.0             # a cloud passes slowly
    puffs = [circle(10 + drift, 34, 8), circle(20 + drift, 30, 10), circle(31 + drift, 35, 7), round_rect(4 + drift, 34, 34, 8, 4)]
    pen.outline(*puffs)
    for p in puffs:
        pen.fill(p, pen.paper)
    pen.shade(round_rect(8 + drift, 37, 28, 5, 2.5), 0.3)


@icon("download", words=("descarga", "descargas", "descargar", "download", "downloads", "downloading", "bajada",
                         "descargando"))
def download(pen: Pen) -> None:
    tray = round_rect(-42, 6, 84, 38, 7)
    pen.outline(tray)
    pen.fill(tray, pen.dark)
    slot = poly([(-34, 13), (-18, 13), (-11, 23), (11, 23), (18, 13), (34, 13), (34, 35), (-34, 35)])
    pen.part(slot, pen.paper)
    pen.shade(rect(-34, 28, 68, 7), 0.3)
    track = round_rect(-28, 29, 56, 4, 2)
    pen.fill(track, pen.muted)
    done = 0.25 + 0.7 * (pen.phase() if pen.alive else 0.8)
    pen.fill(round_rect(-28, 29, 56 * done, 4, 2), pen.color)
    pen.led(36, 39, 2.4, offset=0.2)
    bob = 5 * math.sin(TAU * pen.phase()) if pen.alive else 0.0
    arrow = poly([(-9, -44), (9, -44), (9, -16), (20, -16), (0, 6), (-20, -16), (-9, -16)]).moved(0, bob)
    for k in range(3):                                                       # bits that follow it in
        f = pen.phase(k / 3) if pen.alive else 0.3 * k
        y = -50 + f * 20 + bob
        pen.fill(rect(-22 + 18 * (k % 2) + 4 * k, y, 4, 4), pen.paper, 1 - f)
    pen.outline(arrow)
    pen.fill(arrow, pen.color)
    pen.shade(poly([(0, -44), (9, -44), (9, -16), (20, -16), (0, 6)]).moved(0, bob), 0.3)
    pen.fill(rect(-5, -40 + bob, 3, 20), pen.paper, 0.6)
    pen.flash(arrow)


@icon("gauge", words=("velocidad", "velocidades", "velocimetro", "speed", "speeds", "speedometer", "gauge", "megas",
                      "mbps", "megabits", "ancho de banda", "bandwidth", "latencia", "latency", "ping", "rendimiento",
                      "performance", "speed test"))
def gauge(pen: Pen) -> None:
    house = circle(0, 4, 42)
    pen.outline(house)
    pen.fill(house, pen.dark)
    for a in (225, 315, 45, 135):                                            # screws
        r = math.radians(a)
        pen.fill(circle(37 * math.cos(r), 4 + 37 * math.sin(r), 1.8), pen.paper, 0.6)
    face = circle(0, 4, 34)
    pen.part(face, pen.paper)
    pen.shade(smooth([(8, 36), (26, 26), (34, 8), (20, 28)]), 0.3)
    for a0, a1, col in ((150, 240, pen.muted), (240, 330, pen.color), (330, 390, pen.alert)):
        pen.stroke(_arc(0, 4, 26, a0, a1, 12), col, 6, cap="butt")
    for k in range(13):
        a = math.radians(150 + k * 20)
        r0 = 18 if k % 2 == 0 else 20.5
        _line(pen, 22.5 * math.cos(a), 4 + 22.5 * math.sin(a), r0 * math.cos(a), 4 + r0 * math.sin(a), pen.ink,
              2.2 if k % 2 == 0 else 1.3)
    level = 0.92 if pen.active > 0.2 else (0.55 + 0.33 * math.sin(TAU * pen.phase()) if pen.alive else 0.7)
    a = math.radians(150 + 240 * level)
    tip = (24 * math.cos(a), 4 + 24 * math.sin(a))
    side = (3.4 * math.cos(a + math.pi / 2), 3.4 * math.sin(a + math.pi / 2))
    needle = poly([tip, (side[0], 4 + side[1]), (-6 * math.cos(a), 4 - 6 * math.sin(a)), (-side[0], 4 - side[1])])
    pen.stroke(needle, pen.ink, pen.line)
    pen.fill(needle, pen.alert)
    pen.part(circle(0, 4, 5), pen.dark)
    pen.fill(circle(0, 4, 1.8), pen.paper)
    pen.part(round_rect(-12, 18, 24, 10, 2.5), pen.dark)                     # a little readout
    for k in range(4):
        h = 2 + 5 * (0.5 + 0.5 * math.sin(TAU * pen.phase(k * 0.17)))
        pen.fill(rect(-8 + k * 4.5, 26 - h, 3, h), pen.color)
    pen.flash(face)


def _person(pen: Pen, x: float, y: float, s: float, shirt: str, hair: str, phone: bool, look: float) -> None:
    shoulders = smooth([(x - 26 * s, y + 40 * s), (x - 23 * s, y + 18 * s), (x - 9 * s, y + 8 * s), (x + 9 * s, y + 8 * s),
                        (x + 23 * s, y + 18 * s), (x + 26 * s, y + 40 * s)], tension=0.45)
    head = circle(x, y - 10 * s, 14 * s)
    pen.outline(shoulders, head)
    pen.fill(rect(x - 5 * s, y, 10 * s, 9 * s), pen.skin)
    pen.fill(shoulders, shirt)
    pen.shade(smooth([(x + 8 * s, y + 40 * s), (x + 22 * s, y + 20 * s), (x + 26 * s, y + 40 * s)]), 0.3)
    pen.fill(head, pen.skin)
    top = smooth([(x - 15 * s, y - 8 * s), (x - 14 * s, y - 21 * s), (x - 3 * s, y - 27 * s), (x + 11 * s, y - 24 * s),
                  (x + 15 * s, y - 9 * s), (x + 10 * s, y - 17 * s), (x - 2 * s, y - 19 * s), (x - 10 * s, y - 15 * s)],
                 tension=0.5)
    pen.part(top, hair, thin=False)
    down = 2.5 * s if phone else 0.0
    for ex in (-5, 5):
        pen.fill(circle(x + (ex + look) * s, y - 8 * s + down, 1.7 * s), pen.ink)
    pen.stroke(smooth([(x - 4 * s, y - 1 * s + down), (x, y + 1.5 * s + down), (x + 4 * s, y - 1 * s + down)],
                      closed=False), pen.ink, 1.6)
    if phone:
        device = round_rect(x - 6 * s, y + 14 * s, 12 * s, 18 * s, 2.5 * s)
        pen.part(device, pen.dark)
        pen.fill(round_rect(x - 4 * s, y + 16 * s, 8 * s, 13 * s, 1.5 * s), pen.color, 0.95)
        pen.fill(oval(x - 7 * s, y + 30 * s, 4 * s, 3 * s), pen.skin)
        pen.fill(oval(x + 7 * s, y + 30 * s, 4 * s, 3 * s), pen.skin)


@icon("crowd", words=("gente", "personas", "multitud", "muchedumbre", "vecinos", "vecino", "usuarios", "familia",
                      "crowd", "crowds", "people", "users", "neighbors", "neighbours", "family", "todo el mundo",
                      "everyone", "everybody"))
def crowd(pen: Pen) -> None:
    look = 1.0 * math.sin(TAU * pen.phase()) if pen.alive else 0.0
    _person(pen, -25, -12, 0.78, pen.muted, pen.ink, True, look)
    _person(pen, 25, -14, 0.78, pen.color, pen.metal, True, -look)
    _person(pen, 0, 0, 1.0, pen.dark, pen.ink, False, look)
    for k, (x, y) in enumerate(((-40, -34), (40, -38))):                     # their phones light up
        if pen.on(0.3 * k + 0.1, 0.5):
            pen.stroke(_arc(x, y, 5, 200, 340, 6), pen.color, 2.2)
            pen.stroke(_arc(x, y, 9, 200, 340, 6), pen.color, 2.2)


@icon("video", words=("video", "videos", "pelicula", "peliculas", "streaming", "reproductor", "player", "movie",
                      "movies", "film", "films", "youtube", "netflix", "tiktok", "cargando", "loading", "buffering",
                      "buffer", "reel", "reels"))
def video(pen: Pen) -> None:
    frame = round_rect(-46, -36, 92, 70, 9)
    pen.outline(frame)
    pen.fill(frame, pen.dark)
    screen = round_rect(-40, -30, 80, 46, 4)
    pen.part(screen, pen.color)
    pen.fill(poly([(-40, 16), (-22, -2), (-10, 8), (6, -8), (40, 14), (40, 16)]), pen.ink, 0.25)     # a faint picture
    pen.shade(rect(-40, 6, 80, 10), 0.25)
    pen.fill(circle(-31, -21, 3), pen.alert, 1.0 if pen.on(0.0, 0.5) else 0.3)
    play = circle(0, -7, 12)
    pen.stroke(play, pen.ink, pen.line * 2)
    pen.fill(play, pen.paper)
    pen.fill(poly([(-4, -14), (8, -7), (-4, 0)]), pen.ink)
    turn = 360 * pen.phase() if pen.alive else 40                           # it is loading
    pen.stroke(_arc(0, -7, 18, turn, turn + 110, 10), pen.ink, 6.5)
    pen.stroke(_arc(0, -7, 18, turn, turn + 110, 10), pen.paper, 3.6)
    track = round_rect(-36, 22, 60, 5, 2.5)
    pen.fill(track, pen.muted)
    buffered = 0.35 + 0.25 * (pen.phase(0.3) if pen.alive else 0.5)
    pen.fill(round_rect(-36, 22, 60 * buffered, 5, 2.5), pen.paper, 0.85)
    pen.fill(round_rect(-36, 22, 60 * 0.3, 5, 2.5), pen.alert)
    pen.part(circle(-36 + 60 * 0.3, 24.5, 4), pen.paper)
    pen.fill(rect(30, 21, 3, 7), pen.paper, 0.7)
    pen.fill(rect(35, 21, 3, 7), pen.paper, 0.7)
    pen.flash(screen)


# ── everyday tech ────────────────────────────────────────────────────────────
@icon("cable", words=("cable", "cables", "enchufe", "enchufes", "plug", "plugs", "conector", "connector", "usb",
                      "fibra", "fiber", "fibre", "ethernet", "fibra optica", "cableado", "wired"))
def cable(pen: Pen) -> None:
    cord = smooth([(0, 24), (4, 34), (22, 40), (32, 28), (20, 20), (10, 34), (-12, 44), (-46, 42)], closed=False, tension=0.5)
    pen.cable(cord, 7, pen.color)
    pen.stroke(cord, pen.paper, 1.4, 0.35)
    prongs = [rect(-11, -40, 6, 18), rect(5, -40, 6, 18)]
    body = round_rect(-18, -24, 36, 36, 8)
    relief = poly([(-9, 11), (9, 11), (5, 26), (-5, 26)])
    pen.outline(*prongs, body, relief)
    for p in prongs:
        pen.fill(p, pen.metal)
    for x in (-8, 8):
        pen.fill(circle(x, -34, 1.6), pen.ink, 0.7)
    pen.fill(relief, pen.dark)
    for y in (15, 19, 23):
        pen.fill(rect(-6, y, 12, 1.4), pen.paper, 0.35)
    pen.fill(body, pen.dark)
    pen.shade(round_rect(4, -24, 14, 36, 6), 0.4)
    for y in (-14, -8, -2):                                                  # grip lines
        pen.fill(round_rect(-12, y, 18, 2.4, 1.2), pen.paper, 0.35)
    pen.led(9, 4, 2.4, offset=0.4)
    pen.flash(body)
    if pen.on(0.05, 0.3) or pen.active > 0.2:                                # a spark at the prongs
        for x in (-8, 8):
            for a in (-130, -90, -50):
                r = math.radians(a)
                _line(pen, x + 4 * math.cos(r), -42 + 4 * math.sin(r), x + 9 * math.cos(r), -42 + 9 * math.sin(r),
                      pen.paper, 2.0)


@icon("satellite", words=("satelite", "satelites", "satellite", "satellites", "orbita", "orbit", "espacial"))
def satellite(pen: Pen) -> None:
    for k in range(3):                                                       # the signal it beams down
        if pen.on(k * 0.2, 0.6):
            pen.stroke(_arc(-10, 30, 10 + k * 7, 100, 170, 8), pen.color, 3)
    with saved(pen.cv):
        bob = 2.5 * math.sin(TAU * pen.phase()) if pen.alive else 0.0
        pen.cv.translate(4, bob - 6)
        pen.cv.rotate(-24)
        struts = [poly([(-30, 0), (30, 0)], closed=False)]
        for s in struts:
            pen.cable(s, 3)
        panels = [rect(-48, -11, 32, 22), rect(16, -11, 32, 22)]
        body = round_rect(-12, -15, 24, 30, 4)
        dish = smooth([(-10, 18), (0, 30), (10, 18)], tension=0.5)
        pen.outline(*panels, body, dish)
        for px in (-48, 16):
            pen.fill(rect(px, -11, 32, 22), pen.color)
            pen.shade(rect(px, 0, 32, 11), 0.3)
            for i in range(1, 4):
                pen.stroke(poly([(px + i * 8, -11), (px + i * 8, 11)], closed=False), pen.paper, 1.3, 0.6)
            pen.stroke(poly([(px, 0), (px + 32, 0)], closed=False), pen.paper, 1.3, 0.6)
            pen.flash(rect(px, -11, 32, 22))
        pen.fill(body, pen.metal)
        for y in (-8, -1, 6):
            pen.fill(rect(-12, y, 24, 2), pen.ink, 0.25)
        pen.fill(round_rect(-6, -11, 12, 6, 2), pen.paper, 0.85)
        pen.led(6, 10, 2, offset=0.3)
        pen.fill(dish, pen.paper)
        pen.stroke(poly([(0, 24), (0, 34)], closed=False), pen.ink, 2)
        pen.fill(circle(0, 35, 2), pen.alert)


@icon("keyboard", words=("teclado", "teclados", "keyboard", "keyboards", "tecla", "teclas", "keystroke"))
def keyboard(pen: Pen) -> None:
    cord = smooth([(10, -20), (14, -32), (30, -38), (46, -36)], closed=False, tension=0.5)
    pen.cable(cord, 4.5)
    body = round_rect(-48, -20, 96, 44, 6)
    pen.outline(body)
    pen.fill(body, pen.dark)
    pen.shade(rect(-48, 14, 96, 10), 0.35)
    pressed = int((pen.phase() if pen.alive else 0.2) * 9)
    n = 0
    for r, (x0, count) in enumerate(((-43, 10), (-41, 10), (-39, 9))):
        for c in range(count):
            x, y = x0 + c * 9, -15 + r * 9.5
            down = n == pressed * 3 % 29
            key = round_rect(x, y + (1 if down else 0), 7.4, 7.4, 1.6)
            pen.fill(key, pen.color if down else pen.paper, 1.0 if down else 0.92)
            pen.fill(rect(x + 0.6, y + 5.8, 6.2, 1.6), pen.ink, 0.25)
            n += 1
    pen.fill(round_rect(-43, 13.5, 13, 6.5, 1.6), pen.paper, 0.92)
    pen.fill(round_rect(-27, 13.5, 46, 6.5, 1.6), pen.paper, 0.92)            # the space bar
    pen.fill(round_rect(22, 13.5, 21, 6.5, 1.6), pen.paper, 0.92)
    pen.led(42, -23, 1.8, offset=0.1)
    pen.flash(body)


@icon("shield", words=("escudo", "escudos", "shield", "proteccion", "protegido", "protegida", "protection",
                       "protected", "antivirus", "firewall", "cortafuegos", "defensa", "defense"))
def shield(pen: Pen) -> None:
    pts = [(0, -44), (20, -36), (36, -34), (35, 0), (24, 24), (0, 42), (-24, 24), (-35, 0), (-36, -34), (-20, -36)]
    outer = smooth(pts, tension=0.35)
    pen.outline(outer)
    pen.fill(outer, pen.color)
    inner = smooth([(x * 0.76, y * 0.76 - 1) for x, y in pts], tension=0.35)
    pen.stroke(inner, pen.paper, 2.4, 0.8)
    pen.shade(smooth([(0, -42), (34, -34), (34, 0), (22, 24), (0, 41)], tension=0.3), 0.35)
    for x, y in ((-28, -28), (28, -28), (-24, 12), (24, 12)):
        pen.fill(circle(x, y, 2), pen.paper, 0.75)
    check = poly([(-15, 0), (-4, 12), (17, -14)], closed=False)
    pen.stroke(check, pen.ink, 11)
    pen.stroke(check, pen.paper, 5.5)
    if pen.alive:                                                            # a shine passes over it
        x = -60 + 120 * pen.phase()
        with saved(pen.cv):
            pen.cv.clip(outer)
            pen.fill(poly([(x, -46), (x + 10, -46), (x - 10, 44), (x - 20, 44)]), pen.paper, 0.3)
    pen.flash(outer)


@icon("key", words=("llave", "llaves", "key", "keys", "clave", "claves"))
def key(pen: Pen) -> None:
    with saved(pen.cv):
        pen.cv.rotate(-32)
        pen.cable(_arc(-38, -12, 10, 0, 360, 24), 3, pen.metal)              # the key ring
        bow = circle(-24, 0, 17)
        shaft = round_rect(-10, -5, 52, 10, 3)
        teeth = poly([(24, 4), (42, 4), (42, 14), (37, 14), (37, 10), (32, 10), (32, 16), (27, 16), (27, 10), (24, 10)])
        pen.outline(bow, shaft, teeth)
        pen.fill(teeth, pen.color)
        pen.fill(shaft, pen.color)
        pen.fill(bow, pen.color)
        pen.shade(rect(-10, 0, 52, 5), 0.3)
        pen.shade(smooth([(-24, 17), (-10, 8), (-8, -4), (-14, 10)]), 0.35)
        pen.part(circle(-29, -2, 5.5), pen.dark)
        pen.stroke(poly([(-4, -1), (36, -1)], closed=False), pen.ink, 1.4, 0.45)
        pen.stroke(_arc(-24, 0, 12, 200, 260, 6), pen.paper, 2.4, 0.8)
        g = pen.phase() if pen.alive else 0.5
        if g < 0.5:                                                          # a glint slides along it
            _starx = -6 + 96 * g
            star = _star(_starx, -4, 5)
            pen.stroke(star, pen.ink, pen.line)
            pen.fill(star, pen.paper)
        pen.flash(bow)


@icon("bug", words=("bug", "bugs", "error", "errores", "errors", "falla", "fallas", "fallo", "fallos", "virus",
                    "malware", "glitch", "insecto"))
def bug(pen: Pen) -> None:
    wig = 3 * math.sin(TAU * 2 * pen.phase()) if pen.alive else 0.0
    for side in (-1, 1):                                                     # six legs that walk
        for k, y in enumerate((-6, 8, 22)):
            w = wig if (k % 2 == 0) == (side > 0) else -wig
            leg = poly([(side * 16, y), (side * 30, y - 6 + w), (side * 36, y + 4 + w)], closed=False)
            pen.cable(leg, 2.8)
        ant = smooth([(side * 5, -32), (side * 12, -42), (side * 20 + wig * 0.5, -44)], closed=False)
        pen.cable(ant, 2.2)
        pen.part(circle(side * 21 + wig * 0.5, -44, 3), pen.color)
    head = circle(0, -24, 12)
    body = oval(0, 8, 23, 30)
    pen.outline(head, body)
    pen.fill(head, pen.dark)
    pen.fill(body, pen.color)
    pen.shade(oval(8, 14, 14, 22), 0.3)
    pen.stroke(poly([(0, -21), (0, 38)], closed=False), pen.ink, 2.4)
    for x, y, r in ((-10, -2, 4.5), (11, 4, 4), (-9, 18, 3.5), (10, 22, 4.5)):
        pen.fill(circle(x, y, r), pen.dark)
    pen.stroke(_arc(-6, 0, 18, 200, 240, 6), pen.paper, 2.4, 0.7)
    for x in (-5, 5):
        pen.fill(circle(x, -26, 3.4), pen.paper)
        pen.fill(circle(x + 0.8, -26, 1.6), pen.ink)
    pen.flash(body)


@icon("folder", words=("carpeta", "carpetas", "folder", "folders", "directorio", "directory"))
def folder(pen: Pen) -> None:
    tab = round_rect(-42, -34, 32, 12, 4)
    back = round_rect(-42, -26, 84, 60, 5)
    pen.outline(tab, back)
    pen.fill(tab, pen.color)
    pen.fill(back, pen.color)
    pen.shade(back, 0.45)
    lift = 3 * math.sin(TAU * pen.phase()) if pen.alive else 0.0
    sheet = round_rect(-32, -30 - lift, 62, 44, 2)                           # a paper sticks out
    pen.part(sheet, pen.paper)
    for k, w in enumerate((40, 50, 30)):
        pen.fill(rect(-26, -24 - lift + k * 6, w, 2.2), pen.ink, 0.4)
    sheet2 = round_rect(-36, -22, 50, 36, 2)
    pen.part(sheet2, pen.muted)
    front = poly([(-46, -10), (46, -10), (42, 34), (-42, 34)])
    pen.outline(front)
    pen.fill(front, pen.color)
    pen.shade(poly([(-44, 22), (44, 22), (42, 34), (-42, 34)]), 0.35)
    pen.part(round_rect(-14, 0, 28, 10, 2), pen.paper)
    pen.fill(rect(-10, 4, 18, 2), pen.ink, 0.45)
    pen.stroke(poly([(-44, -4), (44, -4)], closed=False), pen.paper, 1.6, 0.5)
    pen.flash(front)


@icon("file", words=("archivo", "archivos", "documento", "documentos", "file", "files", "document", "documents", "pdf",
                     "fichero", "ficheros", "informe", "report"))
def file(pen: Pen) -> None:
    page = poly([(-30, -44), (14, -44), (32, -26), (32, 44), (-30, 44)])
    pen.outline(page)
    pen.fill(page, pen.paper)
    pen.shade(rect(22, -20, 10, 64), 0.25)
    pen.part(poly([(14, -44), (14, -26), (32, -26)]), pen.muted)
    pen.fill(rect(-22, -34, 28, 8), pen.color)
    for k, w in enumerate((44, 36, 44, 28)):
        pen.fill(rect(-22, -18 + k * 7, w, 2.6), pen.ink, 0.45 if k % 2 == 0 else 0.3)
    pen.part(rect(-22, 14, 24, 20), pen.color)                                # a picture in it
    pen.fill(poly([(-22, 34), (-14, 24), (-8, 30), (-2, 22), (2, 34)]), pen.paper, 0.85)
    pen.fill(rect(8, 16, 16, 2.4), pen.ink, 0.35)
    pen.fill(rect(8, 22, 12, 2.4), pen.ink, 0.25)
    clip = smooth([(-24, -36), (-24, -50), (-18, -52), (-14, -50), (-14, -32), (-19, -30), (-20, -40)], closed=False,
                  tension=0.4)
    pen.stroke(clip, pen.ink, 5)
    pen.stroke(clip, pen.metal, 2.4)
    pen.flash(page)


@icon("photo", words=("foto", "fotos", "fotografia", "fotografias", "imagen", "imagenes", "photo", "photos", "picture",
                      "pictures", "image", "images", "galeria", "gallery", "selfie", "selfies"))
def photo(pen: Pen) -> None:
    with saved(pen.cv):
        pen.cv.rotate(10, 0, 0)
        back = round_rect(-30, -40, 64, 76, 3)
        pen.outline(back)
        pen.fill(back, pen.muted)
        pen.fill(rect(-25, -34, 54, 46), pen.dark, 0.6)
    with saved(pen.cv):
        pen.cv.rotate(-6, 0, 0)
        frame = round_rect(-36, -38, 68, 78, 3)
        pen.outline(frame)
        pen.fill(frame, pen.paper)
        pic = rect(-30, -32, 56, 48)
        pen.part(pic, pen.color)
        pen.fill(circle(12, -18, 7), pen.paper, 0.9)
        drift = 5 * math.sin(TAU * pen.phase()) if pen.alive else 0.0
        for cx, cy, r in ((-14 + drift, -20, 5), (-8 + drift, -22, 6.5), (-2 + drift, -20, 4.5)):
            pen.fill(circle(cx, cy, r), pen.paper, 0.85)
        pen.fill(poly([(-30, 16), (-12, -6), (-2, 4), (8, -8), (26, 12), (26, 16)]), pen.dark)
        pen.fill(poly([(-12, -6), (-8, -1), (-15, -2)]), pen.paper, 0.85)
        pen.shade(rect(-30, 8, 56, 8), 0.3)
        pen.flash(pic)
        pen.fill(rect(-26, 26, 30, 3), pen.ink, 0.35)
        tape = poly([(-12, -44), (12, -46), (13, -36), (-11, -34)])
        pen.fill(tape, pen.metal, 0.75)
        pen.stroke(tape, pen.ink, 1.2, 0.5)


@icon("camera", words=("camara", "camaras", "camera", "cameras", "webcam", "lente", "lentes", "lens", "fotografo",
                       "photographer"))
def camera(pen: Pen) -> None:
    hump = round_rect(-22, -34, 30, 16, 4)
    button = round_rect(20, -31, 15, 9, 3)
    body = round_rect(-46, -22, 92, 58, 10)
    pen.outline(hump, button, body)
    pen.fill(hump, pen.dark)
    pen.fill(button, pen.color)
    pen.fill(body, pen.dark)
    pen.fill(rect(-46, -12, 92, 3), pen.paper, 0.2)
    pen.shade(round_rect(-46, 22, 92, 14, 6), 0.4)
    for x in (-50, 50):
        pen.fill(round_rect(x - 2, -16, 4, 8, 1.5), pen.metal)
    flash = round_rect(-39, -16, 16, 9, 2)
    lit = pen.on(0.0, 0.12) or pen.active > 0.5
    pen.part(flash, pen.paper if lit else pen.muted)
    if lit:
        for a in (-150, -120, -90, -60, -30):
            r = math.radians(a)
            _line(pen, -31 + 11 * math.cos(r), -12 + 11 * math.sin(r), -31 + 17 * math.cos(r),
                  -12 + 17 * math.sin(r), pen.paper, 2.4)
    pen.part(circle(4, 7, 24), pen.metal)
    pen.part(circle(4, 7, 18.5), pen.dark)
    pen.part(circle(4, 7, 13), pen.color)
    turn = 360 * pen.phase() if pen.alive else 0.0
    for k in range(6):                                                       # the aperture blades turn
        a = math.radians(turn + k * 60)
        _line(pen, 4 + 5 * math.cos(a), 7 + 5 * math.sin(a), 4 + 12 * math.cos(a + 0.6), 7 + 12 * math.sin(a + 0.6),
              pen.ink, 1.3)
    pen.fill(circle(4, 7, 5), pen.ink)
    pen.fill(circle(-1, 2, 3.2), pen.paper, 0.85)
    pen.led(34, -12, 2.2, offset=0.5, color=pen.alert)
    pen.flash(body)


@icon("mic", words=("microfono", "microfonos", "mic", "microphone", "microphones", "voz", "voice", "podcast",
                    "grabacion", "recording"))
def mic(pen: Pen) -> None:
    for k in range(3):                                                       # it hears: waves on both sides
        f = pen.phase(k / 3) if pen.alive else 0.3 * k
        for side in (-1, 1):
            pen.stroke(_arc(0, -20, 26 + f * 18, -35 if side > 0 else 145, 35 if side > 0 else 215, 8), pen.color,
                       3, 1 - f)
    holder = _arc(0, -14, 24, 0, 180, 16)
    pen.cable(holder, 4.5)
    pen.cable(poly([(0, 10), (0, 34)], closed=False), 5)
    base = round_rect(-22, 33, 44, 9, 4.5)
    capsule = round_rect(-16, -46, 32, 50, 16)
    pen.outline(base, capsule)
    pen.fill(base, pen.dark)
    pen.fill(capsule, pen.metal)
    with saved(pen.cv):
        pen.cv.clip(capsule)
        for y in range(-42, 4, 6):
            pen.stroke(poly([(-16, y), (16, y)], closed=False), pen.ink, 1.2, 0.4)
        for x in range(-12, 16, 6):
            pen.stroke(poly([(x, -46), (x, 4)], closed=False), pen.ink, 1.2, 0.4)
        pen.shade(rect(4, -46, 12, 50), 0.4)
    pen.part(rect(-17, -14, 34, 7), pen.dark)
    pen.led(9, -10.5, 1.8, offset=0.0, color=pen.alert)
    pen.stroke(_arc(0, -30, 11, 200, 250, 6), pen.paper, 2.4, 0.8)
    pen.flash(capsule)


@icon("speaker", words=("altavoz", "parlante", "parlantes", "bocina", "bocinas", "speaker", "speakers", "sonido",
                        "sonidos", "sound", "audio", "volumen", "volume", "musica", "music", "cancion", "song"))
def speaker(pen: Pen) -> None:
    for k in range(3):
        f = pen.phase(k / 3) if pen.alive else 0.3 * k
        pen.stroke(_arc(14, 12, 24 + f * 14, -40, 40, 8), pen.color, 3.2, 1 - f)
    cab = round_rect(-34, -44, 52, 88, 6)
    pen.outline(cab)
    pen.fill(cab, pen.dark)
    pen.shade(rect(6, -44, 12, 88), 0.4)
    for x, y in ((-29, -39), (13, -39), (-29, 39), (13, 39)):
        pen.fill(circle(x, y, 1.8), pen.paper, 0.6)
    pen.part(circle(-8, -24, 10), pen.metal)
    pen.part(circle(-8, -24, 5.5), pen.ink)
    pen.fill(circle(-10, -26, 1.8), pen.paper, 0.7)
    pump = 1 + (0.06 * math.sin(TAU * 4 * pen.phase()) if pen.alive else 0.0)
    pen.part(circle(-8, 14, 22), pen.metal)
    pen.part(circle(-8, 14, 17.5 * pump), pen.color)
    pen.shade(circle(-4, 18, 12 * pump), 0.3)
    pen.part(circle(-8, 14, 7 * pump), pen.dark)
    pen.fill(circle(-11, 11, 2.2), pen.paper, 0.6)
    pen.led(12, -6, 2, offset=0.25)
    pen.flash(cab)


@icon("message", words=("mensaje", "mensajes", "message", "messages", "chat", "chats", "whatsapp", "sms", "mensajeria",
                        "messaging", "texto", "textos", "comentario", "comentarios", "comment", "comments", "dm"))
def message(pen: Pen) -> None:
    b1 = round_rect(-46, -38, 64, 42, 13)
    t1 = poly([(-36, 0), (-42, 14), (-24, 2)])
    pen.outline(b1, t1)
    pen.fill(t1, pen.color)
    pen.fill(b1, pen.color)
    pen.shade(round_rect(-46, -8, 64, 12, 6), 0.3)
    for k, w in enumerate((40, 28)):
        pen.fill(round_rect(-36, -28 + k * 9, w, 4, 2), pen.paper, 0.85)
    b2 = round_rect(-12, -4, 58, 38, 13)
    t2 = poly([(36, 30), (44, 44), (24, 32)])
    pen.outline(b2, t2)
    pen.fill(t2, pen.paper)
    pen.fill(b2, pen.paper)
    pen.shade(round_rect(-12, 24, 58, 10, 5), 0.25)
    for k in range(3):                                                       # someone is typing
        hop = 3 * max(0.0, math.sin(TAU * pen.phase(-k * 0.12))) if pen.alive else 0.0
        pen.fill(circle(5 + k * 12, 15 - hop, 4), pen.ink, 0.75)
    pen.flash(b2)
    if pen.on(0.4, 0.5):
        pen.fill(circle(16, -36, 5.4), pen.ink)
        pen.fill(circle(16, -36, 4), pen.alert)


@icon("bell", words=("campana", "campanas", "campanita", "bell", "bells", "notificacion", "notificaciones", "notification",
                     "notifications", "aviso", "avisos", "timbre"))
def bell(pen: Pen) -> None:
    p = pen.phase() if pen.alive else 0.6
    swing = 14 * math.sin(TAU * 3 * p) * max(0.0, 1 - p * 2.2)
    with saved(pen.cv):
        pen.cv.rotate(swing, 0, -34)
        clapper = circle(-swing * 0.3, 32, 6.5)
        body = smooth([(-32, 22), (-25, -2), (-20, -22), (0, -32), (20, -22), (25, -2), (32, 22)], tension=0.5)
        rim = round_rect(-36, 18, 72, 9, 4.5)
        knob = circle(0, -35, 5)
        pen.outline(clapper, body, rim, knob)
        pen.fill(clapper, pen.dark)
        pen.fill(knob, pen.dark)
        pen.fill(body, pen.color)
        pen.shade(smooth([(8, 18), (18, -10), (22, 18)]), 0.35)
        pen.stroke(smooth([(-16, 12), (-15, -10), (-8, -22)], closed=False), pen.paper, 3, 0.7)
        pen.fill(rim, pen.color)
        pen.shade(rim, 0.3)
        pen.flash(body)
    if abs(swing) > 2:                                                       # it rings
        for side in (-1, 1):
            for k in range(2):
                pen.stroke(_arc(0, -4, 42 + 7 * k, -40 if side > 0 else 190, -10 if side > 0 else 220, 5), pen.paper,
                           2.6, 0.8)
    pen.fill(circle(25, -26, 9 + pen.line), pen.ink)
    pen.fill(circle(25, -26, 9), pen.alert)
    pen.fill(rect(23.6, -31, 2.8, 6.5), pen.paper)
    pen.fill(circle(25, -21.5, 1.5), pen.paper)


@icon("charger", words=("cargador", "cargadores", "charger", "chargers", "adaptador", "adaptadores", "adapter",
                        "carga rapida", "fast charging"))
def charger(pen: Pen) -> None:
    cord = smooth([(-6, 18), (-4, 32), (16, 40), (36, 30), (38, 18)], closed=False, tension=0.5)
    pen.cable(cord, 5)
    prongs = [rect(-18, -46, 6, 14), rect(-2, -46, 6, 14)]
    brick = round_rect(-28, -34, 44, 52, 9)
    plug = round_rect(32, 2, 12, 18, 3.5)
    pen.outline(*prongs, brick, plug)
    for p in prongs:
        pen.fill(p, pen.metal)
    pen.fill(plug, pen.dark)
    pen.fill(rect(35, -2, 6, 5), pen.metal)
    pen.fill(rect(34, 8, 8, 1.6), pen.paper, 0.4)
    pen.fill(brick, pen.paper)
    pen.shade(round_rect(2, -34, 14, 52, 7), 0.35)
    bolt = poly([(-2, -24), (-16, -6), (-7, -6), (-11, 8), (4, -11), (-5, -11)])
    glow = 0.5 + 0.5 * math.sin(TAU * pen.phase()) if pen.alive else 1.0
    pen.stroke(bolt, pen.paper, pen.line * 2.2)
    pen.part(bolt, pen.color, thin=False)
    pen.fill(bolt, pen.paper, 0.35 * (1 - glow))
    pen.led(8, 10, 2.2, offset=0.1)
    pen.fill(round_rect(-14, 12, 12, 3, 1.5), pen.ink, 0.35)
    pen.flash(brick)


@icon("trash", words=("basura", "papelera", "papeleras", "trash", "bin", "garbage", "bote de basura", "reciclaje",
                      "recycle", "desechos", "waste bin"))
def trash(pen: Pen) -> None:
    lift = max(0.0, math.sin(TAU * pen.phase())) * 6 if pen.alive else 0.0
    body = poly([(-28, -18), (28, -18), (22, 44), (-22, 44)])
    paper = poly([(-18, -18), (-20, -28), (-12, -32), (-6, -26), (2, -34), (8, -24), (4, -18)])
    pen.outline(paper)
    pen.fill(paper, pen.paper)
    pen.stroke(poly([(-12, -28), (-8, -21), (0, -27)], closed=False), pen.ink, 1.2, 0.5)
    pen.outline(body)
    pen.fill(body, pen.color)
    pen.shade(poly([(8, -18), (28, -18), (22, 44), (6, 44)]), 0.35)
    for x in (-13, 0, 13):                                                   # ribs
        pen.stroke(poly([(x, -10), (x * 0.82, 36)], closed=False), pen.ink, 3, 0.35)
        pen.stroke(poly([(x - 1.5, -10), (x * 0.82 - 1.5, 36)], closed=False), pen.paper, 1.6, 0.5)
    with saved(pen.cv):
        pen.cv.rotate(-8 * lift / 6, 32, -22)
        lid = round_rect(-34, -27 - lift, 68, 10, 3.5)
        handle = round_rect(-10, -35 - lift, 20, 9, 3.5)
        pen.outline(lid, handle)
        pen.fill(handle, pen.dark)
        pen.fill(lid, pen.color)
        pen.fill(rect(-30, -24 - lift, 50, 2), pen.paper, 0.45)
    pen.flash(body)


@icon("search", words=("lupa", "lupas", "busqueda", "busquedas", "buscador", "buscadores", "search", "searches",
                       "magnifier", "google", "investigacion", "research"))
def search(pen: Pen) -> None:
    a = TAU * pen.phase() if pen.alive else 0.0
    dx, dy = 3 * math.cos(a), 3 * math.sin(a)                                # it looks around
    with saved(pen.cv):
        pen.cv.translate(dx, dy)
        handle = poly([(14, 14), (38, 38)], closed=False)
        pen.cable(handle, 12)
        pen.stroke(poly([(26, 26), (36, 36)], closed=False), pen.metal, 6, cap="butt")
        ring = circle(-8, -8, 29)
        pen.outline(ring)
        pen.fill(ring, pen.color)
        lens = circle(-8, -8, 22)
        pen.part(lens, pen.paper)
        with saved(pen.cv):
            pen.cv.clip(lens)
            for k, w in enumerate((30, 22, 34, 16)):                          # what it reads, enlarged
                pen.fill(round_rect(-26 - dx, -22 + k * 9 - dy, w, 4.5, 2), pen.ink, 0.4)
            pen.fill(circle(10 - dx, 10 - dy, 7), pen.color, 0.6)
            pen.shade(circle(2, 4, 18), 0.25)
        pen.stroke(_arc(-8, -8, 16, 200, 260, 8), pen.paper, 3.4)
        pen.flash(lens)


@icon("gear", words=("ajustes", "ajuste", "configuracion", "settings", "setting", "engranaje", "engranajes", "gear",
                     "gears", "mecanismo", "mechanism", "configurar", "setup"))
def gear(pen: Pen) -> None:
    turn = 360 * pen.phase() / 8 if pen.alive else 0.0
    small = _gear_path(26, 24, 19, 14, 7, -turn * 8 / 7 + 13)
    big = _gear_path(-10, -8, 34, 27, 10, turn)
    pen.outline(small)
    pen.fill(small, pen.metal)
    pen.part(circle(26, 24, 9), pen.paper)
    pen.fill(circle(26, 24, 4), pen.ink)
    pen.outline(big)
    pen.fill(big, pen.color)
    pen.shade(smooth([(-10, 24), (14, 10), (22, -10), (8, 12)]), 0.35)
    pen.part(circle(-10, -8, 15), pen.dark)
    for k in range(5):
        a = math.radians(turn + k * 72)
        pen.fill(circle(-10 + 10 * math.cos(a), -8 + 10 * math.sin(a), 2.2), pen.paper, 0.6)
    pen.part(circle(-10, -8, 5.5), pen.paper)
    pen.flash(big)


def _dollar(pen: Pen, x: float, y: float, s: float, sx: float = 1.0) -> None:
    ess = smooth([(x + 6 * s * sx, y - 7 * s), (x, y - 9 * s), (x - 6 * s * sx, y - 5 * s), (x, y), (x + 6 * s * sx, y + 5 * s),
                  (x, y + 9 * s), (x - 6 * s * sx, y + 7 * s)], closed=False, tension=0.5)
    pen.stroke(ess, pen.ink, 2.8 * s)
    pen.stroke(poly([(x, y - 13 * s), (x, y + 13 * s)], closed=False), pen.ink, 2.4 * s)


@icon("money", words=("dinero", "plata", "moneda", "monedas", "pago", "pagos", "precio", "precios", "costo", "costos",
                      "money", "coin", "coins", "cash", "price", "prices", "cost", "costs", "dolar", "dolares", "dollar",
                      "dollars", "euro", "euros", "billete", "billetes", "factura", "facturas", "tarifa", "ahorros",
                      "savings", "sueldo", "salary"))
def money(pen: Pen) -> None:
    with saved(pen.cv):
        pen.cv.rotate(-12, -10, -12)
        note = round_rect(-46, -32, 64, 34, 3)
        pen.outline(note)
        pen.fill(note, pen.color)
        pen.stroke(round_rect(-42, -28, 56, 26, 2), pen.paper, 1.4, 0.6)
        pen.fill(oval(-14, -15, 9, 10), pen.paper, 0.8)
        _dollar(pen, -14, -15, 0.6)
        for x, y in ((-38, -24), (10, -24), (-38, -6), (10, -6)):
            pen.fill(circle(x, y, 2.4), pen.paper, 0.6)
    for k in range(4):                                                       # a stack of coins
        y = 36 - k * 7
        side = round_rect(-40, y - 3, 36, 8, 4)
        pen.outline(side)
        pen.fill(side, pen.metal)
        for i in range(6):
            pen.fill(rect(-36 + i * 5.5, y - 1, 1.4, 5), pen.ink, 0.3)
    top = oval(-22, 12, 18, 5)
    pen.part(top, pen.metal)
    pen.stroke(oval(-22, 12, 12, 3.2), pen.ink, 1.2, 0.4)
    spin = abs(math.cos(TAU * pen.phase())) if pen.alive else 1.0          # a coin that spins
    sx = max(spin, 0.12)
    coin = oval(24, 18, 20 * sx, 20)
    pen.outline(coin)
    pen.fill(coin, pen.metal)
    pen.stroke(oval(24, 18, 15 * sx, 15), pen.ink, 1.4, 0.5)
    pen.shade(oval(28 * 1.0, 22, 12 * sx, 14), 0.3)
    if sx > 0.45:
        _dollar(pen, 24, 18, 0.9, sx)
    pen.flash(coin)


@icon("calendar", words=("calendario", "calendarios", "fecha", "fechas", "calendar", "calendars", "date", "dates", "mes",
                         "meses", "month", "months", "semana", "semanas", "week", "weeks", "agenda", "schedule",
                         "fin de semana", "weekend"))
def calendar(pen: Pen) -> None:
    under = round_rect(-36, -28, 76, 74, 6)
    page = round_rect(-40, -32, 76, 74, 6)
    pen.outline(under, page)
    pen.fill(under, pen.muted)
    pen.fill(page, pen.paper)
    pen.fill(round_rect(-40, -32, 76, 20, 6), pen.color)
    pen.fill(rect(-40, -20, 76, 8), pen.color)
    pen.shade(rect(-40, -22, 76, 10), 0.3)
    pen.stroke(poly([(-40, -12), (36, -12)], closed=False), pen.ink, pen.line * 0.6)
    for x in (-20, 16):                                                      # the rings
        pen.cable(_arc(x, -32, 6, 180, 360, 10), 3, pen.metal)
    mark = int(pen.phase() * 4) if pen.alive else 1
    for r in range(4):
        for c in range(5):
            x, y = -34 + c * 13.5, -6 + r * 11
            pen.fill(round_rect(x, y, 9, 7, 1.5), pen.ink, 0.15 if (r + c) % 6 else 0.3)
    picked = ((2, 1), (3, 2), (1, 3), (4, 1))[mark % 4]
    cx, cy = -34 + picked[0] * 13.5 + 4.5, -6 + picked[1] * 11 + 3.5
    pen.fill(round_rect(cx - 4.5, cy - 3.5, 9, 7, 1.5), pen.alert, 0.8)
    pen.stroke(oval(cx, cy, 9, 7.5), pen.alert, 2.4)
    pen.flash(page)


@icon("pin", words=("mapa", "mapas", "ubicacion", "ubicaciones", "gps", "map", "maps", "location", "locations", "pin",
                    "direccion", "address", "zona", "zonas", "barrio", "neighborhood", "ciudad", "city"))
def pin(pen: Pen) -> None:
    sheet = poly([(-46, 14), (-16, 8), (14, 16), (46, 8), (46, 40), (14, 46), (-16, 38), (-46, 46)])
    pen.outline(sheet)
    pen.fill(sheet, pen.paper)
    pen.shade(poly([(-16, 8), (14, 16), (14, 46), (-16, 38)]), 0.3)
    pen.stroke(poly([(-16, 8), (-16, 38)], closed=False), pen.ink, 1.2, 0.5)
    pen.stroke(poly([(14, 16), (14, 46)], closed=False), pen.ink, 1.2, 0.5)
    road = smooth([(-46, 30), (-26, 24), (0, 34), (24, 26), (46, 30)], closed=False)
    pen.stroke(road, pen.muted, 3.4)
    pen.stroke(smooth([(-30, 46), (-24, 30), (-28, 12)], closed=False), pen.muted, 2.4)
    pen.stroke(smooth([(30, 10), (28, 28), (34, 44)], closed=False), pen.color, 2.4, 0.7)
    hop = abs(math.sin(TAU * pen.phase())) * 6 if pen.alive else 0.0
    pen.fill(oval(0, 28, 9 - hop * 0.6, 3), pen.ink, 0.45)
    drop = smooth([(0, 26), (-12, 4), (-19, -14), (-13, -32), (0, -38), (13, -32), (19, -14), (12, 4)], tension=0.5)
    drop = drop.moved(0, -hop)
    pen.outline(drop)
    pen.fill(drop, pen.color)
    pen.shade(smooth([(0, 26), (12, 4), (19, -14), (14, -6), (4, 12)], tension=0.5).moved(0, -hop), 0.35)
    pen.part(circle(0, -16 - hop, 7.5), pen.paper)
    pen.stroke(_arc(0, -16 - hop, 14, 200, 250, 6), pen.paper, 2.4, 0.7)
    pen.flash(drop)


@icon("rocket", words=("cohete", "cohetes", "rocket", "rockets", "despegue", "despegar", "liftoff", "lanzamiento",
                       "launch"))
def rocket(pen: Pen) -> None:
    p = pen.phase() if pen.alive else 0.5
    for k in range(3):                                                       # smoke
        f = (p + k / 3) % 1.0
        r = 7.5 * math.sin(math.pi * min(0.15 + f, 1.0))
        if r > 1:
            puff = circle((k - 1) * 12 * (0.4 + f), 40 + f * 5, r)
            pen.outline(puff)
            pen.fill(puff, pen.paper)
            pen.shade(circle((k - 1) * 12 * (0.4 + f) + r * 0.3, 40 + f * 5 + r * 0.3, r * 0.6), 0.3)
    bob = 1.5 * math.sin(TAU * 2 * p)
    with saved(pen.cv):
        pen.cv.translate(0, bob)
        flick = 0.75 + 0.25 * math.sin(TAU * 4 * p)
        flame = smooth([(-7, 22), (0, 22 + 22 * flick), (7, 22)], tension=0.4)
        pen.stroke(flame, pen.ink, pen.line * 2)
        pen.fill(flame, pen.alert)
        pen.fill(smooth([(-3.5, 22), (0, 22 + 12 * flick), (3.5, 22)], tension=0.4), pen.paper)
        fins = [poly([(-12, 2), (-26, 18), (-25, 27), (-10, 18)]), poly([(12, 2), (26, 18), (25, 27), (10, 18)])]
        body = smooth([(0, -46), (12, -30), (14, 4), (10, 22), (-10, 22), (-14, 4), (-12, -30)], tension=0.45)
        nozzle = round_rect(-8, 18, 16, 6, 2)
        pen.outline(*fins, body, nozzle)
        for f in fins:
            pen.fill(f, pen.color)
        pen.fill(nozzle, pen.dark)
        pen.fill(body, pen.paper)
        with saved(pen.cv):
            pen.cv.clip(body)
            pen.fill(rect(-16, -50, 32, 22), pen.color)
            pen.shade(rect(4, -50, 14, 75), 0.35)
            pen.fill(rect(-16, 8, 32, 3), pen.ink, 0.25)
        pen.stroke(poly([(-12, -28), (12, -28)], closed=False), pen.ink, pen.line * 0.6)
        pen.part(circle(0, -12, 7.5), pen.metal)
        pen.part(circle(0, -12, 4.8), pen.color)
        pen.fill(circle(-1.6, -13.6, 1.6), pen.paper, 0.9)
        pen.stroke(poly([(0, 2), (0, 18)], closed=False), pen.ink, 2.4, 0.6)
        pen.flash(body)


@icon("idea", words=("idea", "ideas", "bombilla", "bombillas", "bombillo", "foco", "focos", "bulb", "lightbulb", "truco",
                     "trucos", "tip", "tips", "consejo", "consejos", "solucion", "soluciones", "solution", "hack",
                     "hacks", "trick", "tricks"))
def idea(pen: Pen) -> None:
    for k in range(8):                                                       # rays that pulse
        a = math.radians(-180 + k * 25.7 - 90 + 90)
        if math.sin(a) > 0.35:
            continue
        pulse = 0.8 + 0.2 * math.sin(TAU * pen.phase(k * 0.12)) if pen.alive else 1.0
        x0, y0 = 32 * math.cos(a), -14 + 32 * math.sin(a)
        x1, y1 = (32 + 10 * pulse) * math.cos(a), -14 + (32 + 10 * pulse) * math.sin(a)
        pen.stroke(poly([(x0, y0), (x1, y1)], closed=False), pen.ink, 5 + pen.line)
        pen.stroke(poly([(x0, y0), (x1, y1)], closed=False), pen.color, 5)
    glass = smooth([(0, -42), (20, -36), (27, -16), (18, 2), (12, 14), (-12, 14), (-18, 2), (-27, -16), (-20, -36)],
                   tension=0.5)
    rings = [round_rect(-12, 14, 24, 7, 3), round_rect(-11, 21, 22, 7, 3), round_rect(-9, 28, 18, 6, 3)]
    tip = poly([(-5, 34), (5, 34), (2, 40), (-2, 40)])
    pen.outline(glass, *rings, tip)
    pen.fill(tip, pen.dark)
    for r in rings:
        pen.part(r, pen.metal)
    pen.fill(glass, pen.color)
    pen.fill(circle(0, -16, 14), pen.paper, 0.35 + 0.15 * math.sin(TAU * pen.phase()) if pen.alive else 0.4)
    pen.shade(smooth([(6, 12), (18, 0), (26, -16), (16, -4)]), 0.3)
    fil = poly([(-6, 12), (-6, -8), (-3, -14), (0, -8), (3, -14), (6, -8), (6, 12)], closed=False)
    pen.stroke(fil, pen.ink, 2.2)
    pen.stroke(_arc(0, -16, 19, 200, 250, 8), pen.paper, 3.2, 0.9)
    pen.flash(glass)


@icon("warning", words=("alerta", "alertas", "advertencia", "advertencias", "cuidado", "peligro", "peligros", "warning",
                        "warnings", "alert", "alerts", "danger", "riesgo", "riesgos", "risk", "risks", "problema",
                        "problemas", "problem", "problems", "ojo"))
def warning(pen: Pen) -> None:
    tri = poly([(0, -42), (44, 36), (-44, 36)])
    if pen.on(0.0, 0.5) or pen.active > 0.2:                                 # it flashes
        for side in (-1, 1):
            for k in range(3):
                a = math.radians(-90 + side * (35 + k * 22))
                _line(pen, side * 6 + 34 * math.cos(a), -18 + 34 * math.sin(a), side * 6 + 44 * math.cos(a),
                      -18 + 44 * math.sin(a), pen.paper, 3)
    pen.outline(tri)
    pen.stroke(tri, pen.ink, pen.line * 3.4)
    pen.fill(tri, pen.color)
    inner = poly([(0, -28), (33, 29), (-33, 29)])
    pen.stroke(inner, pen.ink, 1.6, 0.5)
    pen.shade(poly([(0, -42), (44, 36), (10, 36)]), 0.3)
    pen.fill(round_rect(-4.8, -16, 9.6, 30, 4.8), pen.ink)
    pen.fill(circle(0, 23, 5), pen.ink)
    pen.flash(tri)
