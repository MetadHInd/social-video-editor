"""Things on a screen: `code` (a terminal or an editor where the lines get typed) and `screen` (a
screenshot in a device or browser frame that zooms to the part that matters).

Same family as the built-in templates (templates.py): the window is there from the first frame, the
content comes in, one thing is the point, and the picture keeps living once it has settled.
"""
from __future__ import annotations

import math
import re

from ..models import PlanError
from . import kit, motion
from .canvas import Canvas, saved
from .paths import circle, cut, dashes, poly, rect, round_rect
from .scene import Scene, template
from .spec import number, required
from .templates import _baseline, _bob
from .theme import ink

SHELLS = {"", "sh", "bash", "zsh", "fish", "shell", "terminal", "console", "cmd", "powershell", "ps", "cli"}
KEYWORDS = frozenset("""
    and as async await break case catch class const continue def del do elif else except export extends false
    finally fn for from func function if import in interface is lambda let match new nil none not null or
    package pass pub raise return self static struct super switch this throw true try type use var void
    while with yield True False None
    sudo cd ls echo export git npm npx pip pip3 python python3 brew apt curl wget cat grep mkdir rm cp mv
    chmod docker kubectl node yarn cargo go make ssh
""".split())
_TOKEN = re.compile(r"""(?P<comment>\#.*$|//.*$)|(?P<string>"(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?)"""
                    r"""|(?P<number>\b\d+(?:\.\d+)?\b)|(?P<flag>(?<![\w-])--?[A-Za-z][\w-]*)|(?P<word>[A-Za-z_][\w.-]*)"""
                    r"""|(?P<space>\s+)|(?P<other>.)""")


def code_lines(value) -> list[str]:
    """Lines of code: a list as given; a text is split on line breaks (real ones or a written "\\n"),
    else on ";" (so a one-line cue can hold several commands)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).rstrip() for v in value]
    text = str(value).replace("\\n", "\n")
    parts = text.split("\n") if "\n" in text else text.split(";")
    lines = [p.rstrip() for p in parts]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return [line.strip() if "\n" not in text else line for line in lines]


def tokens(line: str, shell: bool) -> list[tuple[str, str]]:
    """(text, kind) pieces of a line for coloring: comment, string, number, flag, keyword, command,
    word, space, other. In a shell the first word is the command."""
    out, first = [], True
    for m in _TOKEN.finditer(line):
        kind, txt = m.lastgroup or "other", m.group()
        if kind == "word":
            if shell and first:
                kind = "command"
            elif txt in KEYWORDS:
                kind = "keyword"
        if kind not in ("space",):
            first = False if kind != "other" or txt not in "|&;" else True
        out.append((txt, kind))
    return out


def _wrap(line: str, cols: int) -> list[tuple[int, int]]:
    """A line of code in rows of at most `cols` characters, broken after a space when one is near:
    (start, end) character ranges. Rows after the first are indented two more than the line
    (continuation_indent). An empty line is one empty row."""
    out, a, width = [], 0, cols
    while len(line) - a > width:
        b = a + width
        for marks in (" ", "-/._,=("):                       # after a space, else after a sign, else anywhere
            at = max(line.rfind(m, a + 1, a + width) for m in marks)
            if at > a + width // 3:
                b = at + 1
                break
        out.append((a, b))
        a, width = b, cols - continuation_indent(line, cols)
    out.append((a, len(line)))
    return out


def continuation_indent(line: str, cols: int) -> int:
    """Characters a wrapped row of a line is indented by: the line's own indent plus two."""
    return min(len(line) - len(line.lstrip(" ")) + 2, cols // 2)


def _wrap_text(line: str, cols: int) -> list[str]:
    return [line[a:b] for a, b in _wrap(line, cols)]


# ── code ─────────────────────────────────────────────────────────────────────
@template("code")
class Code(Scene):
    """A terminal or an editor: the lines are typed one by one with a cursor, the output prints after
    them, and one line can be marked. A shell (`lang` empty, bash, zsh…) gets a prompt before each
    line; any other language gets line numbers.

    lines="npm install; npm run build" (or `text` with line breaks) · lang (bash, python, js…) ·
    prompt ("$" in a shell) · output (what it prints; lines like `lines`) · highlight (line number or
    words of a line) · file (name on the window bar) · title · color
    """

    default_dur = 5.0
    scales = (1.0,)                      # it sizes itself: letters as large as the longest line allows

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        given = sp.get("lines") if sp.get("lines") not in (None, "", []) else sp.get("text")
        if given in (None, "", []):
            raise PlanError("Graphic 'code' needs 'lines' (or 'text')")
        self.code = code_lines(given)
        if not 1 <= len(self.code) <= 12:
            raise PlanError(f"Graphic 'code' takes 1 to 12 lines, not {len(self.code)}")
        self.output = code_lines(sp.get("output"))
        if len(self.output) > 8:
            raise PlanError(f"Graphic 'code' shows up to 8 output lines, not {len(self.output)}")
        self.lang = str(sp.get("lang") or "").strip().lower()
        self.shell = self.lang in SHELLS
        self.prompt = str(sp.get("prompt") if sp.get("prompt") is not None else ("$" if self.shell else "")).strip()
        self.color, self.ink = ink(st.theme, sp.get("color"))
        self.file = str(sp.get("file") or (self.lang if self.lang and not self.shell else "terminal"))
        self.hi = self._highlight(sp.get("highlight"))
        x0, y0, x1, y1 = st.safe
        self.title_size = z(84 if st.vertical else 60)
        self.title_rows = kit.balanced(st, str(sp["title"]), "display", self.title_size, st.safe_w) if sp.get("title") else []
        title_h = len(self.title_rows) * self.title_size * 1.1 + z(60) if self.title_rows else 0
        inline_out = self.shell and bool(self.output)            # a shell prints under the commands
        panel = bool(self.output) and not inline_out             # an editor's output goes on a paper slip below
        self.inline_out = inline_out
        self.bar_h = z(66)
        self.pad = z(34)
        # the letters: as large as the longest line allows; below a readable size long lines wrap instead
        num_w = 100 * 0.62 * (len(str(len(self.code))) + 1.6) if not self.prompt else 0
        prompt_w = st.measure(self.prompt + " ", "mono", 100) if self.prompt else 0
        gut = num_w or prompt_w
        widest = max([st.measure(line, "mono", 100) for line in self.code]
                     + [st.measure(line, "mono", 100) - gut for line in self.output if inline_out] + [100 * 6])
        inner = st.safe_w - 2 * self.pad
        size = min(100 * inner / (widest + gut), z(60 if st.vertical else 44))
        size = max(size, z(50 if st.vertical else 30))
        room = st.safe_h * 0.93 - title_h - self.bar_h - 2 * self.pad
        while True:
            cols = max(int((inner - gut * size / 100) / max(st.measure("M" * 20, "mono", size) / 20, 1e-6)), 8)
            self.wrapped = [_wrap(line, cols) for line in self.code]
            self.out_rows = [part for line in self.output for part in _wrap_text(line, cols)]
            rows = sum(len(w) for w in self.wrapped) + (len(self.out_rows) + 0.4 if inline_out else 0) + 1
            panel_rows = (len(self.out_rows) + 1.2) * 0.9 if panel else 0
            if (rows + panel_rows) * size * 1.55 + (z(60) if panel else 0) <= room or size <= z(18):
                break
            size *= 0.92
        self.size, self.cols = size, cols
        self.lh = self.size * 1.55
        self.gutter = gut * self.size / 100
        win_w = st.safe_w if st.vertical else min(st.safe_w, max(st.safe_w * 0.74, (widest + gut) * self.size / 100
                                                                  + 2 * self.pad))
        body = rows * self.lh + 2 * self.pad
        self.panel_h = (len(self.out_rows) * self.lh * 0.9 + self.lh * 1.3) if panel else 0.0
        target = st.safe_h * (0.9 if st.vertical else 0.86)      # a tall window, so the frame is filled
        body = max(body, target - title_h - self.bar_h - (self.panel_h + z(50) if panel else 0))
        win_h = self.bar_h + body
        height = title_h + win_h + (self.panel_h + z(50) if panel else 0)
        self.block = (win_w, height)
        top = (y0 + y1) / 2 - height / 2
        self.title_y = top + self.title_size * 0.9
        self.win = ((x0 + x1) / 2 - win_w / 2, top + title_h, win_w, win_h)
        self.panel_y = top + title_h + win_h + z(50)
        first, self.first_row = 0, []
        for w in self.wrapped:
            self.first_row.append(first)
            first += len(w)
        self.code_rows = first
        # timing: each line is typed at an even pace; Enter, then the next one; the output prints after
        chars = sum(len(line) for line in self.code) or 1
        budget = max(min(self.dur * 0.5, chars * 0.055), 0.5)
        self.cps = chars / budget
        t = 0.45
        self.line_t = []
        for line in self.code:
            self.line_t.append(t)
            t += len(line) / self.cps + (0.16 if line.strip() else 0.05)
        self.typed = t
        self.out_t = [self.typed + 0.12 + i * 0.07 for i in range(len(self.out_rows))]
        self.mark_t = (self.out_t[-1] if self.out_t else self.typed) + 0.18
        self.settle = self.mark_t + 0.5 if self.hi is not None else self.mark_t + 0.2

    def _highlight(self, value) -> int | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        if text.isdigit():
            idx = int(text)
            if not 1 <= idx <= len(self.code):
                raise PlanError(f"Graphic 'code': highlight {idx} is not a line (1 to {len(self.code)})")
            return idx - 1
        for i, line in enumerate(self.code):
            if text.lower() in line.lower():
                return i
        raise PlanError(f"Graphic 'code': highlight '{text}' is not in any line")

    def _tone(self, kind: str) -> tuple[str, float]:
        c = self.st.theme["colors"]
        return {"comment": (c["muted"], 1.0), "string": (c["alert"], 1.0), "number": (c["metal"], 1.0),
                "flag": (c["paper"], 0.68), "keyword": (self.color, 1.0), "command": (self.color, 1.0)}.get(kind, (c["paper"], 1.0))

    def _typed_line(self, cv: Canvas, line: str, x: float, y: float, a: int, b: int) -> float:
        """Characters a…b of a line, colored by token. → where the text ends."""
        st, pos = self.st, 0
        for txt, kind in tokens(line, self.shell):
            lo, hi = max(a, pos), min(b, pos + len(txt))
            pos += len(txt)
            if hi <= lo:
                continue
            part = txt[lo - (pos - len(txt)):hi - (pos - len(txt))]
            if part.strip():
                color, alpha = self._tone(kind)
                cv.text(part, x, y, "mono", self.size, color, alpha)
            x += st.measure(part, "mono", self.size)
        return x

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x0 = st.safe[0]
        tx = x0 if st.vertical else self.win[0]
        for k, row in enumerate(self.title_rows):
            kit.text(cv, st, row, tx, self.title_y + k * self.title_size * 1.1, "display", self.title_size, c["paper"])
        if self.title_rows:
            y = self.title_y + (len(self.title_rows) - 1) * self.title_size * 1.1 + z(28)
            kit.thread(cv, st, kit.squiggle(tx, tx + z(230), y, z(6)), motion.ease_out(motion.progress(t, 0.05, 0.4)))
        x, y, w, h = self.win
        cx, cy = x + w / 2, y + h / 2
        kit.glow(cv, st, cx, cy, max(w, h) * 0.42, self.color, t)
        scale = motion.stamp_scale(motion.progress(t, 0, 0.4), 0.85)
        radius = st.px(float(st.theme["box"].get("radius", 0))) + z(8)
        with kit.boiled(cv, st, 90, t, cx, cy), kit.tilted(cv, kit.box_tilt(st) * 0.6, cx, cy, scale):
            kit.shape(cv, st, round_rect(x, y, w, h, radius), c["device"], halo=False, with_shadow=True)
            cv.fill(round_rect(x, y, w, self.bar_h, radius), c["ink"], 0.35)          # the window bar
            cv.fill(rect(x, y + self.bar_h - z(3), w, z(3)), c["ink"], 0.5)
            for i, dot in enumerate((c["alert"], c["metal"], self.color)):
                cv.fill(circle(x + z(36) + i * z(32), y + self.bar_h / 2, z(10)), dot, 0.9)
            kit.text(cv, st, self.file, cx, _baseline(y + self.bar_h / 2, z(26)), "label", z(26), c["muted"], "center")
            with saved(cv):
                cv.clip(rect(x, y + self.bar_h, w, h - self.bar_h))
                self._body(cv, t)
        if self.panel_h:
            self._panel(cv, t)
        self._marker(cv, t)
        kit.sparkles(cv, st, (x, y, w, h), t, seed=21, size=z(22), appear=motion.progress(t, self.typed, 0.3))

    def _body(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x, y, w, h = self.win
        left = x + self.pad
        tx = left + self.gutter
        base = y + self.bar_h + self.pad + self.size * 0.95
        digits = len(str(len(self.code)))
        cursor = None
        for i, (line, start, parts, row0) in enumerate(zip(self.code, self.line_t, self.wrapped, self.first_row)):
            ly = base + row0 * self.lh
            if self.hi == i:                                                    # the marked line
                mp = motion.ease_out(motion.progress(t, self.mark_t, 0.35))
                if mp > 0:
                    band = rect(x + z(8), ly - self.size * 1.02, (w - z(16)) * mp, self.lh * len(parts))
                    cv.fill(band, self.color, 0.2)
                    cv.fill(rect(x + z(8), ly - self.size * 1.02, z(8), self.lh * len(parts)), self.color)
            if self.prompt:
                if t >= start - 0.15:
                    cv.text(self.prompt, left, ly, "mono", self.size, self.color)
            else:
                cv.text(str(i + 1).rjust(digits), left, ly, "mono", self.size, c["muted"], 0.6)
            if t < start:
                continue
            n = min(len(line), int((t - start) * self.cps + 1e-6))
            for k, (a, b) in enumerate(parts):
                if n <= a and k:
                    break
                ind = st.measure(" " * continuation_indent(line, self.cols), "mono", self.size) if k else 0.0
                end = self._typed_line(cv, line, tx + ind, ly + k * self.lh, a, min(n, b))
                last_row = ly + k * self.lh
            if n < len(line) or i == len(self.code) - 1 or t < self.line_t[i + 1]:
                cursor = (end, last_row, n < len(line))
        if not self.prompt:                                                     # editor: the empty lines are numbered too
            k = self.code_rows
            while base + k * self.lh < y + h:
                cv.text(str(len(self.code) + k - self.code_rows + 1).rjust(digits), left, base + k * self.lh, "mono",
                        self.size, c["muted"], 0.3)
                k += 1
        oy = base + self.code_rows * self.lh + self.lh * 0.25
        if self.inline_out:
            for i, (line, start) in enumerate(zip(self.out_rows, self.out_t)):
                if t >= start:
                    kit.text(cv, st, line, tx, oy + i * self.lh, "mono", self.size, c["paper"], alpha=0.72,
                             p=motion.clamp01((t - start) / 0.12))
        if t >= self.typed and self.prompt and self.shell:                     # a new prompt waits for the next command
            ny = oy + (len(self.out_rows) + 0.15) * self.lh if self.inline_out else base + self.code_rows * self.lh
            if not self.out_rows or t >= self.out_t[-1] + 0.1:
                cv.text(self.prompt, left, ny, "mono", self.size, self.color)
                cursor = (tx, ny, False)
        if cursor:                                                              # solid while typing, blinking when idle
            cxp, cyp, typing = cursor
            alive = st.theme["motion"].get("ambient", True)
            on = typing or not alive or (motion.loop(t) * 4) % 1 < 0.55
            if on:
                cv.fill(rect(cxp + z(3), cyp - self.size * 0.82, self.size * 0.55, self.size * 1.0), self.color, 0.9)

    def _panel(self, cv: Canvas, t: float) -> None:
        """An editor's output, printed on a paper slip under the window."""
        st, c, z = self.st, self.st.theme["colors"], self.z
        x, _, w, _ = self.win
        pw = w * 0.86
        px = x + (w - pw) / 2
        p = motion.progress(t, self.out_t[0] - 0.1, 0.3)
        if p <= 0:
            return
        with kit.tilted(cv, kit.box_tilt(st, 1) * 1.4, px + pw / 2, self.panel_y, 0.6 + 0.4 * motion.ease_out(p)):
            kit.box(cv, st, px, self.panel_y, pw, self.panel_h)
            cv.fill(rect(px, self.panel_y, z(12), self.panel_h), self.color)
            for i, (line, start) in enumerate(zip(self.out_rows, self.out_t)):
                if t >= start:
                    kit.text(cv, st, line, px + z(40), self.panel_y + self.lh * 0.65 + (i + 0.4) * self.lh * 0.9,
                             "mono", self.size * 0.9, c["ink"], p=motion.clamp01((t - start) / 0.12))

    def _marker(self, cv: Canvas, t: float) -> None:
        """The marker sits on the window's corner and steps to the marked line once it is marked."""
        st, z = self.st, self.z
        x, y, w, _ = self.win
        home = (x + w - z(40), y - z(4))
        if self.hi is None or t < self.mark_t:
            kit.draw_element("marker", cv, st, *home, t, scale=1.1 * self.size / z(40))
            return
        ly = y + self.bar_h + self.pad + self.size * 0.95 + self.first_row[self.hi] * self.lh - self.size * 0.35
        hop = motion.ease_in_out(motion.progress(t, self.mark_t, 0.35))
        mx, my = motion.lerp(home[0], x + w - z(4), hop), motion.lerp(home[1], ly, hop) - z(40) * math.sin(math.pi * hop)
        kit.draw_element("marker", cv, st, mx, my + _bob(self, t, hop >= 1), t, scale=1.1 * self.size / z(40))

    def sounds(self):
        keys = [("keystroke", s + k / self.cps) for line, s in zip(self.code, self.line_t) for k in range(0, len(line), 3)]
        return [("whoosh", 0.0)] + keys + ([("pop", self.mark_t)] if self.hi is not None else [])

    def lines(self):
        return [x for x in (" ".join(self.title_rows), *self.code, *self.out_rows) if x.strip()]


# ── screen ───────────────────────────────────────────────────────────────────
FRAMES = ("browser", "phone", "window", "none")


@template("screen")
class Screen(Scene):
    """A screenshot in a device or browser frame that zooms to the part that matters, with a ring on it.
    When the frame leaves a band of the picture empty (a wide screenshot in a vertical video, a phone
    screenshot in a landscape one), the zoomed part is shown there instead, in a loupe joined to the ring.

    image (file, relative to the project like a photo) · focus_x, focus_y (0-1: where to look; default
    the middle) · zoom (how much closer; default 2.2) · frame (browser, phone, window, none; default by
    the picture's shape) · url (browser bar) · label (a note by the ring) · title · source · color
    """

    default_dur = 4.5
    scales = (1.0,)                      # it sizes itself: the frame as large as the picture's shape allows

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.image = str(required(sp, "image", "screen"))
        size = st.image_size(self.image)
        if size is None:
            raise PlanError(f"Graphic 'screen': image not found or not readable: {self.image}")
        iw, ih = size
        self.aspect = iw / ih if ih else 1.0
        self.fx, self.fy = self._fraction(sp, "focus_x"), self._fraction(sp, "focus_y")
        zoom = number(sp.get("zoom")) if sp.get("zoom") not in (None, "") else 2.2
        if zoom is None or not 1.0 <= zoom <= 8.0:
            raise PlanError(f"Graphic 'screen': 'zoom' must be a number from 1 to 8, not {sp.get('zoom')!r}")
        self.zoom = zoom
        frame = str(sp.get("frame") or ("phone" if self.aspect < 0.8 else "browser")).lower()
        if frame not in FRAMES:
            raise PlanError(f"Graphic 'screen': frame '{frame}' is not one of {', '.join(FRAMES)}")
        self.frame = frame
        self.url = str(sp.get("url") or "")
        self.label = str(sp.get("label") or "")
        self.source = str(sp.get("source") or "")
        self.color, self.ink = ink(st.theme, sp.get("color"))
        x0, y0, x1, y1 = st.safe
        self.title_size = z(84 if st.vertical else 64)
        self.title_rows = kit.balanced(st, str(sp["title"]), "display", self.title_size, st.safe_w) if sp.get("title") else []
        title_h = len(self.title_rows) * self.title_size * 1.1 + z(56) if self.title_rows else 0
        source_h = z(60) if self.source else 0
        avail_w, avail_h = st.safe_w, st.safe_h * self.fill - title_h - source_h
        # the frame around the screen: bar on top (browser / window), a bezel (phone), a paper border (none)
        if frame == "phone":
            self.bezel = (z(22), z(70), z(22), z(54))          # left, top, right, bottom
        elif frame in ("browser", "window"):
            self.bezel = (z(10), z(70 if frame == "browser" else 58), z(10), z(10))
        else:
            self.bezel = (z(14), z(14), z(14), z(14))
        bl, bt, br, bb = self.bezel
        fw, fh = self._fit(avail_w, avail_h)
        # a loupe in the band the frame leaves empty: below a wide picture, beside a tall one
        self.loupe = None
        gap = z(80)
        label_room = z(100) if self.label else 0.0           # the note goes under the loupe
        if (avail_h - fh) / avail_h > 0.22:
            fw, fh = self._fit(avail_w, avail_h * 0.56)
            lh = avail_h - fh - gap - label_room
            lw = min(avail_w * 0.94, lh * 2.2)
            self.loupe = ("below", lw, lh)
        elif (avail_w - fw) / avail_w > 0.3:
            fw, fh = self._fit(avail_w * (0.48 if st.vertical else 0.42), avail_h)
            lw = avail_w - fw - gap
            lh = min(avail_h * 0.86 - label_room, lw * (1.5 if st.vertical else 0.8))
            lw = min(lw, lh * 1.4)
            self.loupe = ("beside", lw, lh)
        self.block = (fw, fh + title_h + source_h)
        top = (y0 + y1) / 2 - (avail_h + title_h + source_h) / 2
        self.title_y = top + self.title_size * 0.9
        area_y = top + title_h
        if self.loupe is None:
            fx = (x0 + x1) / 2 - fw / 2
            fy = area_y + (avail_h - fh) / 2
        elif self.loupe[0] == "below":
            fx, fy = (x0 + x1) / 2 - fw / 2, area_y
        else:                                               # the frame and the loupe, centered as a pair
            fx, fy = (x0 + x1) / 2 - (fw + gap + self.loupe[1]) / 2, area_y + (avail_h - fh) / 2
        self.gap = gap
        self.frame_box = (fx, fy, fw, fh)
        self.view = (fx + bl, fy + bt, fw - bl - br, fh - bt - bb)
        vx, vy, vw, vh = self.view
        # the region looked at, in fractions of the image: 1/zoom² of its area, with the shape of what shows it
        target = self._loupe_aspect() or (vw / vh)
        rw = min(math.sqrt(target / self.aspect) / self.zoom, 1.0)
        rh = min(rw * self.aspect / target, 1.0)
        self.region = (min(max(self.fx - rw / 2, 0.0), 1 - rw), min(max(self.fy - rh / 2, 0.0), 1 - rh), rw, rh)
        if self.loupe:
            kind, lw, lh = self.loupe
            if kind == "below":
                self.loupe_box = ((x0 + x1) / 2 - lw / 2, fy + fh + gap, lw, lh)
            else:
                self.loupe_box = (fx + fw + gap, area_y + (avail_h - lh - label_room) / 2, lw, lh)
        self.source_y = top + title_h + avail_h + source_h * 0.7
        self.ring_t, self.zoom_t, self.zoom_dur = 0.45, 0.95, 0.8
        self.settle = self.zoom_t + self.zoom_dur + 0.4

    def _loupe_aspect(self) -> float | None:
        return self.loupe[1] / self.loupe[2] if self.loupe else None

    @staticmethod
    def _fraction(sp: dict, key: str) -> float:
        if sp.get(key) in (None, ""):
            return 0.5
        v = number(sp.get(key))
        if v is None or not 0.0 <= v <= 1.0:
            raise PlanError(f"Graphic 'screen': '{key}' must be a fraction from 0 to 1, not {sp.get(key)!r}")
        return v

    def _fit(self, w: float, h: float) -> tuple[float, float]:
        """The largest frame (bezel included) that holds the screenshot inside w × h."""
        bl, bt, br, bb = self.bezel
        sw = min(w - bl - br, (h - bt - bb) * self.aspect)
        return sw + bl + br, sw / self.aspect + bt + bb

    def _camera(self, t: float) -> float:
        """How far the frame has zoomed in (0-1); the loupe keeps the frame as it is."""
        if self.loupe:
            return 0.0
        return motion.ease_in_out(motion.progress(t, self.zoom_t, self.zoom_dur))

    def _image_box(self, t: float) -> tuple[float, float, float, float]:
        """Where the whole screenshot is drawn: the viewport at rest; zoomed around the focus, never
        leaving an edge of the viewport empty, and drifting gently once there."""
        vx, vy, vw, vh = self.view
        p = self._camera(t)
        if p <= 0:
            return vx, vy, vw, vh
        alive = self.st.theme["motion"].get("ambient", True)
        drift = (motion.wave(t, 0.0) - 0.5) if alive and t >= self.settle else 0.0
        end = max(self.zoom * 0.72, 1.0)                               # the region fills 72 % of the screen
        s = motion.lerp(1.0, end * (1 + 0.025 * drift), p)
        rx, ry, rw, rh = self.region
        fx, fy = rx + rw / 2, ry + rh / 2
        w, h = vw * s, vh * s
        tx = motion.lerp(vx + fx * vw, vx + vw / 2 + drift * vw * 0.02, p)
        ty = motion.lerp(vy + fy * vh, vy + vh / 2, p)
        x = min(max(tx - fx * w, vx + vw - w), vx)
        y = min(max(ty - fy * h, vy + vh - h), vy)
        return x, y, w, h

    def _ring_rect(self, t: float) -> tuple[float, float, float, float]:
        x, y, w, h = self._image_box(t)
        rx, ry, rw, rh = self.region
        return x + rx * w, y + ry * h, rw * w, rh * h

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x0 = st.safe[0]
        for k, row in enumerate(self.title_rows):
            kit.text(cv, st, row, x0, self.title_y + k * self.title_size * 1.1, "display", self.title_size, c["paper"])
        if self.title_rows:
            y = self.title_y + (len(self.title_rows) - 1) * self.title_size * 1.1 + z(28)
            kit.thread(cv, st, kit.squiggle(x0, x0 + z(230), y, z(6)), motion.ease_out(motion.progress(t, 0.05, 0.4)))
        fx, fy, fw, fh = self.frame_box
        cx, cy = fx + fw / 2, fy + fh / 2
        kit.glow(cv, st, cx, cy, max(fw, fh) * 0.4, self.color, t)
        scale = motion.stamp_scale(motion.progress(t, 0, 0.4), 0.85)
        with kit.tilted(cv, kit.box_tilt(st) * 0.5, cx, cy, scale):
            self._frame(cv, t)
            vx, vy, vw, vh = self.view
            with saved(cv):
                cv.clip(rect(vx, vy, vw, vh))
                cv.fill(rect(vx, vy, vw, vh), c["paper"])
                cv.image(self.image, *self._image_box(t))
                self._ring(cv, t)
        if self.loupe:
            self._loupe(cv, t)
        else:
            self._note(cv, t)
        kit.source_line(cv, st, self.source, x0, self.source_y, p=kit.printed(t, 0.3), size=z(26))

    def _frame(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        fx, fy, fw, fh = self.frame_box
        bl, bt, br, bb = self.bezel
        if self.frame == "phone":
            r = min(fw, fh) * 0.12
            kit.shape(cv, st, round_rect(fx, fy, fw, fh, r), c["device"], halo=False, with_shadow=True)
            cv.fill(round_rect(fx + fw / 2 - fw * 0.15, fy + bt * 0.34, fw * 0.3, bt * 0.32, bt * 0.16), c["ink"])
            cv.fill(round_rect(fx + fw / 2 - fw * 0.17, fy + fh - bb * 0.55, fw * 0.34, bb * 0.14, bb * 0.07), c["muted"], 0.6)
            for dy, hh in ((0.18, 0.08), (0.3, 0.12)):
                cv.fill(rect(fx - z(7), fy + fh * dy, z(8), fh * hh), c["device"])
            return
        radius = st.px(float(st.theme["box"].get("radius", 0))) + z(10)
        if self.frame == "none":
            kit.box(cv, st, fx, fy, fw, fh)
            return
        kit.shape(cv, st, round_rect(fx, fy, fw, fh, radius), c["device"], halo=False, with_shadow=True)
        dot_y = fy + bt / 2
        for i, dot in enumerate((c["alert"], c["metal"], self.color)):
            cv.fill(circle(fx + z(34) + i * z(30), dot_y, z(9)), dot, 0.9)
        if self.frame == "browser":
            ux, uw = fx + z(140), fw - z(180)
            if uw > z(120):
                cv.fill(round_rect(ux, dot_y - z(20), uw, z(40), z(20)), c["paper"], 0.92)
                cv.fill(circle(ux + z(22), dot_y, z(7)), c["muted"])
                if self.url:
                    size = kit.fit_size(st, self.url, "label", z(24), uw - z(60))
                    kit.text(cv, st, self.url, ux + z(40), _baseline(dot_y, size), "label", size, c["ink"], alpha=0.75)

    def _ring(self, cv: Canvas, t: float) -> None:
        """A rounded ring around the region, drawn by hand, then its dashes keep marching."""
        st, c, z = self.st, self.st.theme["colors"], self.z
        p = motion.ease_out(motion.progress(t, self.ring_t, 0.45))
        if p <= 0:
            return
        x, y, w, h = self._ring_rect(t)
        m = z(10)
        x, y, w, h = x - m, y - m, w + 2 * m, h + 2 * m
        r = min(w, h) * 0.22
        pts = _round_rect_points(x, y, w, h, r)
        part = cut(pts, p)
        width = z(9)
        if len(part) > 1:
            outline = st.px(float(st.theme["stroke"]["outline"]))
            path = poly(part, closed=False)
            cv.stroke(path, c["ink"], width + 2 * max(outline, z(3)))
            cv.stroke(path, self.color, width)
        if p >= 1 and st.theme["motion"].get("ambient", True):                  # light runs around the ring
            off = motion.loop(t) * 4 * z(60)
            for d in dashes(pts, z(26), z(34), -off):
                cv.stroke(poly(d, closed=False), c["paper"], width * 0.45, 0.85, cap="round")

    def _note(self, cv: Canvas, t: float) -> None:
        """The label beside the ring and the marker on it, once the frame has zoomed in."""
        st, z = self.st, self.z
        landed = motion.progress(t, self.zoom_t + self.zoom_dur, 0.3)
        if landed <= 0:
            return
        x, y, w, h = self._ring_rect(t)
        vx, vy, vw, vh = self.view
        mx = min(max(x + w, vx + z(30)), vx + vw - z(10))
        my = min(max(y, vy + z(20)), vy + vh - z(20))
        if self.label:
            size = kit.fit_size(st, self.label, "label", z(40), vw * 0.8)
            tw = st.measure(self.label, "label", size) + size
            lx = min(max(x + w / 2, vx + tw / 2), vx + vw - tw / 2)
            ly = min(y + h + z(46), vy + vh - z(36))
            kit.tag(cv, st, self.label, lx, ly, size, anchor="center", p=landed, index=1)
        kit.draw_element("marker", cv, st, mx, my + _bob(self, t, landed >= 1), t, scale=self.k * 1.1)
        kit.sparkles(cv, st, (x, y, w, h), t, seed=29, size=z(20), appear=landed)

    def _loupe(self, cv: Canvas, t: float) -> None:
        """The zoomed region in its own box, grown out of the ring and joined to it by two threads."""
        st, c, z = self.st, self.st.theme["colors"], self.z
        p = motion.progress(t, self.zoom_t, self.zoom_dur * 0.7)
        if p <= 0:
            return
        lx, ly, lw, lh = self.loupe_box
        rx, ry, rw, rh = self._ring_rect(t)
        grow = motion.ease_out(p)
        # threads from the ring to the loupe
        if self.loupe[0] == "below":
            ends = [((rx, ry + rh), (lx, ly)), ((rx + rw, ry + rh), (lx + lw, ly))]
        else:
            ends = [((rx + rw, ry), (lx, ly)), ((rx + rw, ry + rh), (lx, ly + lh))]
        for a, b in ends:
            kit.thread(cv, st, [a, b], grow, alpha=0.7)
        if grow >= 1:
            for a, b in ends:
                kit.rail(cv, st, [a, b], t, color=self.color, width=z(4), alpha=0.7)
        # the box grows from the ring to its place
        bx = motion.lerp(rx, lx, grow)
        by = motion.lerp(ry, ly, grow)
        bw, bh = motion.lerp(rw, lw, grow), motion.lerp(rh, lh, grow)
        border = z(12)
        radius = st.px(float(st.theme["box"].get("radius", 0))) + z(12)
        kit.shape(cv, st, round_rect(bx - border, by - border, bw + 2 * border, bh + 2 * border, radius), c["paper"],
                  halo=False, with_shadow=True)
        alive = st.theme["motion"].get("ambient", True)
        drift = (motion.wave(t, 0.3) - 0.5) if alive and t >= self.settle else 0.0
        reg_x, reg_y, reg_w, reg_h = self.region
        s = 1 + 0.03 * drift
        iw, ih = bw / reg_w * s, bh / reg_h * s
        ix = bx - (reg_x + reg_w / 2) * iw + bw / 2 + drift * bw * 0.02
        iy = by - (reg_y + reg_h / 2) * ih + bh / 2
        with saved(cv):
            cv.clip(rect(bx, by, bw, bh))
            cv.fill(rect(bx, by, bw, bh), c["paper"])
            cv.image(self.image, ix, iy, iw, ih)
        cv.stroke(rect(bx, by, bw, bh), self.color, z(8))
        landed = motion.progress(t, self.zoom_t + self.zoom_dur * 0.7, 0.3)
        if landed > 0:
            if self.label:
                size = kit.fit_size(st, self.label, "label", z(42), lw * 0.9)
                kit.tag(cv, st, self.label, lx + lw / 2, ly + lh + border + z(16) + size * 0.8, size, anchor="center",
                        p=landed, index=1)
            kit.draw_element("marker", cv, st, lx + lw - z(6), ly - z(4) + _bob(self, t, landed >= 1), t, scale=self.k * 1.2)
            kit.sparkles(cv, st, (lx, ly, lw, lh), t, seed=31, size=z(22), appear=landed)

    def sounds(self):
        return [("whoosh", 0.0), ("pop", self.ring_t + 0.4), ("whoosh", self.zoom_t)]

    def lines(self):
        return [x for x in (" ".join(self.title_rows), self.label, self.source) if x]


def _round_rect_points(x: float, y: float, w: float, h: float, r: float, per: int = 6) -> list[tuple[float, float]]:
    """The outline of a rounded rectangle as points, starting at the top middle (to draw it by hand)."""
    pts = []
    corners = ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180))
    pts.append((x + w / 2, y))
    for cx, cy, a0 in corners:
        for k in range(per + 1):
            a = math.radians(a0 + 90 * k / per)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append((x + w / 2, y))
    return pts


__all__ = ["Code", "Screen", "code_lines", "tokens"]
