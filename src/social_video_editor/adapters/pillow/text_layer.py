"""Subtitles and on-screen texts drawn with Pillow and laid over the video as a transparent layer.

- Auto subtitles (if there is speech): groups of 2-3 words, the word being said highlighted,
  "pop" animation when they appear. Style: recipe → subtitles.
- Plan texts (hook, labels, cards, counter, CTA…). Styles: recipe → texts.styles.
  Animations: "typewriter" (letter by letter with a cursor; a "description" is typed below after
  the title), "pop" (appears with a bounce) or "none". Color emoji supported.
The timing comes from domain.text_layout; this module draws each state as an image and writes
an ffconcat list of stills with their durations, which FFmpeg overlays on the video.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ...domain.text_layout import Caption, Card, CardState, Layer, resolve_style, text_scale, text_width
from ...domain.text_units import clean
from ..ffmpeg.escape import concat_path
from . import rich_text as rt
from .fonts import load_font

DESC_FONT = "Bradley Hand Bold"


def rgba(c: str | None):
    if not c:
        return None
    c = c.lstrip("#")
    if len(c) == 6:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))


# ── images ───────────────────────────────────────────────────────────────────

def subtitle_image(words: list[str], active: int, style: dict, W: float) -> Image.Image:
    """W: the text width (output width, capped in landscape — see domain.text_layout.text_width)."""
    f = load_font(style.get("font", "Inter-Black"), int(style["size"]))
    stroke = int(style.get("stroke", 8))
    ws = [w.upper() if style.get("uppercase", True) else w for w in words]
    space = f.getlength(" ")
    lines, current = [], []
    for k in range(len(ws)):   # split into lines by words
        trial = current + [k]
        if current and sum(f.getlength(ws[j]) for j in trial) + space * (len(trial) - 1) > W * 0.86:
            lines.append(current)
            current = [k]
        else:
            current = trial
    lines.append(current)
    asc, desc = f.getmetrics()
    line_h = asc + desc
    width = int(max(sum(f.getlength(ws[j]) for j in ids) + space * (len(ids) - 1) for ids in lines)) + 2 * stroke + 20
    height = line_h * len(lines) + 2 * stroke + 24
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(shadow)
    for li, ids in enumerate(lines):
        lw = sum(f.getlength(ws[k]) for k in ids) + space * (len(ids) - 1)
        x, y = (width - lw) / 2, stroke + 6 + li * line_h
        for k in ids:
            col = rgba(style["highlight"]) if k == active else rgba(style["color"])
            if style.get("shadow", True):
                ds.text((x, y + 7), ws[k], font=f, fill=(0, 0, 0, 160), stroke_width=stroke, stroke_fill=(0, 0, 0, 160))
            d.text((x, y), ws[k], font=f, fill=col, stroke_width=stroke, stroke_fill=rgba(style["stroke_color"]))
            x += f.getlength(ws[k]) + space
    if style.get("shadow", True):
        shadow = shadow.filter(ImageFilter.GaussianBlur(6))
        shadow.alpha_composite(img)
        return shadow
    return img


class TextBlock:
    """A paragraph (title or description) already split into lines with the FULL sentence."""

    def __init__(self, text: str, font_name: str, size: int, color: str, max_px: float, upper: bool,
                 line_spacing: float = 1.05):
        t = clean(text)
        self.t = t.upper() if upper else t
        self.font, self.size, self.color = load_font(font_name, size), size, rgba(color)
        self.lines = rt.wrap(self.t, self.font, size, max_px) if self.t else []
        asc, desc = self.font.getmetrics()
        self.asc, self.line_h = asc, int((asc + desc) * line_spacing)
        self.units = [u for ln in self.lines for u in ln]
        self.n = len(self.units)
        self.width = max((rt.width(ln, self.font, size) for ln in self.lines), default=0)
        self.height = self.line_h * len(self.lines)

    def draw(self, img, cx: float, y0: float, visible: int, cursor: bool, stroke: int = 0):
        """Draws the first `visible` units centered on cx, with a cursor after the last visible one."""
        rest, cursor_at = visible, None
        for i, ln in enumerate(self.lines):
            x = cx - rt.width(ln, self.font, self.size) / 2
            y = y0 + i * self.line_h
            part = ln[:max(rest, 0)]
            xf = rt.draw(img, x, y, part, self.font, self.size, self.color, stroke) if part else x
            if rest <= len(ln) and cursor_at is None:
                cursor_at = (xf, y)
            rest -= len(ln)
        if cursor and cursor_at:
            x, y = cursor_at
            ImageDraw.Draw(img).rectangle([x + 3, y + self.asc * 0.12, x + 3 + max(self.size * 0.07, 3),
                                           y + self.asc * 1.08], fill=self.color)


def blocks(text: dict, style: dict, W: int, default_font: str,
           H: int | None = None) -> tuple[TextBlock, TextBlock | None]:
    """Title and description blocks; lines wrap within max_width of the text width (W, capped in landscape)."""
    max_px = text_width(W, H or W) * float(style.get("max_width", 0.84)) - 2 * style["size"] * 0.45
    title = TextBlock(text["text"], style.get("font", default_font), style["size"], style.get("color", "#FFFFFF"),
                      max_px, style.get("uppercase", False))
    desc = None
    if text.get("description"):
        size_d = int(style.get("desc_size", style["size"] * 0.72))
        desc = TextBlock(text["description"], style.get("desc_font", DESC_FONT), size_d,
                         style.get("desc_color", style.get("color", "#FFFFFF")), max_px, False, 1.0)
    return title, desc


def card_image(title: TextBlock, desc: TextBlock | None, style: dict, n_t: int, n_d: int,
               cursor: str | None) -> Image.Image:
    """Box (or outlined text) with title and optional description; n_t/n_d = visible units."""
    size = style["size"]
    pad_x, pad_y = int(size * 0.45), int(size * 0.28)
    sep = int(size * 0.18) if desc else 0
    width = int(max(title.width, desc.width if desc else 0)) + 2 * pad_x
    height = title.height + (desc.height + sep if desc else 0) + 2 * pad_y
    m = 16
    img = Image.new("RGBA", (width + 2 * m, height + 2 * m), (0, 0, 0, 0))
    bg = rgba(style.get("background"))
    radius = int(size * float(style.get("radius", 0.3)))
    if bg:
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([m, m + 6, m + width, m + 6 + height], radius=radius, fill=(0, 0, 0, 70))
        img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(9)))
        ImageDraw.Draw(img).rounded_rectangle([m, m, m + width, m + height], radius=radius, fill=bg,
                                              outline=rgba(style.get("outline")), width=3 if style.get("outline") else 0)
    stroke = 0 if bg else max(int(size / 10), 4)
    cx = m + width / 2
    y = m + pad_y - int(title.asc * 0.08)
    title.draw(img, cx, y, n_t, cursor == "t", stroke)
    if desc:
        desc.draw(img, cx, y + title.height + sep, n_d, cursor == "d", 0 if bg else max(stroke // 2, 3))
    return img


def build_card(text: dict, style: dict, W: int, H: int, default_font: str, idx: int):
    """→ (domain Card with its timing, function CardState → image)."""
    title, desc = blocks(text, style, W, default_font, H)
    anim = text.get("animation", style.get("animation", "pop"))
    pop = anim in ("pop", "typewriter") and text.get("pop", True)
    x, y = float(style.get("x", 0.5)) * W, float(style.get("y", 0.5)) * H
    desc_units = desc.units if desc else None
    if anim == "typewriter":
        speed = float(text.get("speed", style.get("speed", 30)))
        card = Card.typewriter(f"t{idx}", text["start"], text["end"], x, y, title.units, desc_units, speed, pop)
    else:
        card = Card.static(f"t{idx}", text["start"], text["end"], x, y, title.units, desc_units, pop)
    cache: dict = {}

    def image(state: CardState) -> Image.Image:
        k = (state.title, state.description, state.cursor)
        if k not in cache:
            cache[k] = card_image(title, desc, style, *k)
        return cache[k]

    return card, image


# ── the layer ────────────────────────────────────────────────────────────────

class PillowTextRenderer:
    def render_layer(self, texts: list[dict], groups: list[dict], cfg: dict, W: int, H: int, total: float,
                     folder: Path, boxes: list | None = None) -> tuple[Path | None, list[float]]:
        """Generates the images and the ffconcat list → (list path, time of every keystroke).

        boxes: if a list is given, it receives where every text / subtitle group was drawn:
        {"kind": "text" | "subtitle", "text", "x", "y", "w", "h" (pixels, visible pixels only, the
        union over its whole life, pop animation included), "start", "end"}.
        """
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.png"):
            old.unlink()
        scale = text_scale(W, H)          # short side / 1080: vertical output unchanged
        sub = {**cfg["subtitles"], "size": int(cfg["subtitles"]["size"] * scale),
               "stroke": max(int(cfg["subtitles"]["stroke"] * scale), 2)}
        styles = cfg["texts"]["styles"]
        default_font = cfg["subtitles"].get("font", "Inter-Black")
        elements, painters, names = [], {}, {}
        for gi, g in enumerate(groups):
            words = [w["word"] for w in g["words"]]
            names[f"s{gi}"] = ("subtitle", " ".join(words))
            for k, w in enumerate(g["words"]):
                start = g["start"] if k == 0 else w["start"]
                end = g["words"][k + 1]["start"] if k + 1 < len(g["words"]) else g["end"]
                if end <= start:
                    continue
                im = subtitle_image(words, k, sub, text_width(W, H))
                key = f"s{gi}_{k}"
                elements.append(Caption(key, start, end, W / 2, float(sub["y"]) * H,
                                        k == 0 and sub.get("animation") == "pop"))
                painters[key] = lambda state, im=im: im
        for ti, tx in enumerate(texts):
            card, image = build_card(tx, resolve_style(tx, styles, scale), W, H, default_font, ti)
            elements.append(card)
            painters[card.key] = image
            names[card.key] = ("text", tx.get("text", ""))
        if not elements:
            return None, []

        layer = Layer(elements)
        stills, scaled, listing, placed = {}, {}, [], {}
        for a, b, active in layer.frames(total):
            key = tuple((state, sc, e.x, e.y) for e, state, sc in active)
            if boxes is not None:
                _track_boxes(placed, active, painters, scaled, a, b, W, H)
            if key not in stills:
                canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                for e, state, sc in active:
                    im = painters[e.key](state)
                    if sc != 1.0:
                        k2 = (state, sc)
                        if k2 not in scaled:
                            scaled[k2] = im.resize((max(int(im.width * sc), 1), max(int(im.height * sc), 1)),
                                                   Image.LANCZOS)
                        im = scaled[k2]
                    x0 = int(min(max(e.x - im.width / 2, 0), W - im.width))
                    y0 = int(min(max(e.y - im.height / 2, 0), H - im.height))
                    canvas.alpha_composite(im, (max(x0, 0), max(y0, 0)))
                path = folder / f"c{len(stills):05d}.png"
                canvas.save(path, compress_level=1)
                stills[key] = path
            listing.append((stills[key], b - a))
        lst = folder / "layer.ffconcat"
        lines = ["ffconcat version 1.0"]
        for path, d in listing:
            lines += [f"file {concat_path(path.name)}", f"duration {d:.5f}"]
        lines.append(f"file {concat_path(listing[-1][0].name)}")
        lst.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        if boxes is not None:
            for k, (x0, y0, x1, y1, a, b) in placed.items():
                kind, text = names.get(k, ("text", ""))
                boxes.append({"kind": kind, "text": text, "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
                              "start": round(a, 3), "end": round(b, 3)})
            boxes.sort(key=lambda bx: (bx["start"], bx["kind"]))
        return lst, layer.keystrokes()


def _scaled(im: Image.Image, sc: float) -> Image.Image:
    return im if sc == 1.0 else im.resize((max(int(im.width * sc), 1), max(int(im.height * sc), 1)))


def _track_boxes(placed: dict, active, painters: dict, scaled: dict, a: float, b: float, W: int, H: int) -> None:
    """Union of the visible-pixel rectangles of each text (subtitle words grouped) over time."""
    for e, state, sc in active:
        im = painters[e.key](state)
        bb_key = ("bbox", id(im), sc)
        if bb_key not in scaled:
            sim = _scaled(im, sc)
            scaled[bb_key] = (sim.size, sim.getbbox())
        (w, h), bb = scaled[bb_key]
        if bb is None:
            continue
        x0 = max(int(min(max(e.x - w / 2, 0), W - w)), 0)
        y0 = max(int(min(max(e.y - h / 2, 0), H - h)), 0)
        r = (x0 + bb[0], y0 + bb[1], min(x0 + bb[2], W), min(y0 + bb[3], H))
        k = e.key.split("_")[0] if e.key.startswith("s") else e.key
        if k in placed:
            p = placed[k]
            placed[k] = (min(p[0], r[0]), min(p[1], r[1]), max(p[2], r[2]), max(p[3], r[3]), min(p[4], a), max(p[5], b))
        else:
            placed[k] = (*r, a, b)
