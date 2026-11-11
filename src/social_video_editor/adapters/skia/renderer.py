"""Draws graphics with Skia (optional extra `graphics`: pip install skia-python).

SkiaCanvas implements the canvas interface of domain/graphics/canvas.py; SkiaGraphicRenderer turns a
Scene into frames. skia is imported only when a graphic is actually drawn.

A scene says which frames look the same (Scene.frame_state): each distinct state is drawn once and
reused, so a graphic that has settled costs almost nothing per frame.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path as FilePath

import numpy as np

from ...domain.graphics import kit
from ...domain.graphics.paths import Path
from ...domain.graphics.scene import Scene, Stage
from ...domain.models import UserError
from ..system.fonts import find_font_file

MISSING = "skia-python is not installed (pip install \"social-video-editor[graphics]\")"
GRAIN_PHASES = 4
FALLBACK_FAMILIES = ("Inter", "Helvetica Neue", "Arial", "DejaVu Sans")


def _rgba(color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    h = color.lstrip("#")
    a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(round(255 * max(0.0, min(alpha * a, 1.0))))


class FontBook:
    """Theme font roles ("display", "number", "label") → typefaces. A role is a font file name or
    path, or {"file": …, "wght": 900, "wdth": 72} for an instance of a variable font."""

    def __init__(self, skia, fonts: dict):
        self.skia, self.spec = skia, fonts or {}
        self._faces: dict = {}
        self._fonts: dict = {}

    def _fallback(self):
        path, _ = find_font_file("")
        face = self.skia.Typeface.MakeFromFile(str(path)) if path else None
        if face is None:
            for family in FALLBACK_FAMILIES:
                face = self.skia.Typeface(family, self.skia.FontStyle.Bold())
                if face is not None:
                    break
        return face

    def face(self, role: str):
        if role not in self._faces:
            spec = self.spec.get(role)
            axes = {k: v for k, v in spec.items() if k != "file"} if isinstance(spec, dict) else {}
            name = spec.get("file") if isinstance(spec, dict) else spec
            face = None
            if name:
                direct = FilePath(str(name)).expanduser()
                path = direct if direct.is_file() else find_font_file(str(name))[0]
                face = self.skia.Typeface.MakeFromFile(str(path)) if path else None
            if face is not None and axes:
                face = self._instance(face, axes) or face
            self._faces[role] = face or self._fallback()
        return self._faces[role]

    def _instance(self, face, axes: dict):
        """An instance of a variable font (None when this build of skia cannot make it)."""
        sk = self.skia
        try:
            position = sk.FontArguments.VariationPosition
            coords = position.Coordinates([position.Coordinate(int.from_bytes(tag.encode("ascii"), "big"), float(value))
                                           for tag, value in axes.items()])
            args = sk.FontArguments()
            args.setVariationDesignPosition(position(coords))
            return face.makeClone(args)
        except Exception:                # noqa: BLE001  (older bindings: use static font files instead)
            return None

    def font(self, role: str, size: float):
        key = (role, round(size * 4) / 4)
        if key not in self._fonts:
            f = self.skia.Font(self.face(role), key[1])
            f.setSubpixel(True)
            f.setEdging(self.skia.Font.Edging.kAntiAlias)
            self._fonts[key] = f
        return self._fonts[key]

    def measure(self, text: str, role: str, size: float) -> float:
        return float(self.font(role, size).measureText(text))


class ImageStore:
    def __init__(self, skia):
        self.skia = skia
        self._images: dict = {}

    def get(self, file: str):
        if file not in self._images:
            p = FilePath(file).expanduser()
            self._images[file] = self._trimmed(self.skia.Image.open(str(p))) if p.is_file() else None
        return self._images[file]

    def _trimmed(self, img):
        """A cut-out without its transparent margins (so it is as large as its box allows)."""
        if img is None or img.isOpaque():
            return img
        alpha = img.toarray(colorType=self.skia.kRGBA_8888_ColorType)[:, :, 3] > 8
        rows, cols = np.flatnonzero(alpha.any(axis=1)), np.flatnonzero(alpha.any(axis=0))
        if not len(rows) or (len(rows) == img.height() and len(cols) == img.width()):
            return img
        box = self.skia.IRect.MakeLTRB(int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
        return img.makeSubset(box) or img

    def size(self, file: str) -> tuple[int, int] | None:
        img = self.get(file)
        return (img.width(), img.height()) if img is not None else None


class SkiaCanvas:
    def __init__(self, skia, W: int, H: int, fonts: FontBook, images: ImageStore):
        self.sk, self.W, self.H, self.fonts, self.images = skia, W, H, fonts, images
        self.surface = skia.Surface(W, H)
        self.cv = self.surface.getCanvas()
        self._paths: dict = {}
        self._text_paths: dict = {}
        self._tiles: dict = {}
        self._sampling = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)

    # ── frame ────────────────────────────────────────────────────────────────
    def begin(self) -> None:
        self.cv.restoreToCount(1)
        self.cv.clear(self.sk.ColorTRANSPARENT)

    def rgb(self) -> np.ndarray:
        return self.surface.toarray(colorType=self.sk.kRGBA_8888_ColorType)[:, :, :3].copy()

    def rgba(self) -> np.ndarray:
        """Straight (not premultiplied) alpha."""
        img = self.surface.makeImageSnapshot()
        return img.toarray(colorType=self.sk.kRGBA_8888_ColorType, alphaType=self.sk.kUnpremul_AlphaType).copy()

    # ── state ────────────────────────────────────────────────────────────────
    def save(self): self.cv.save()
    def restore(self): self.cv.restore()
    def translate(self, dx, dy): self.cv.translate(dx, dy)
    def rotate(self, degrees, cx=0.0, cy=0.0): self.cv.rotate(degrees, cx, cy)

    def scale(self, sx, sy=None, cx=0.0, cy=0.0):
        self.cv.translate(cx, cy)
        self.cv.scale(sx, sx if sy is None else sy)
        self.cv.translate(-cx, -cy)

    def push_alpha(self, alpha):
        self.cv.saveLayerAlpha(None, int(round(255 * max(0.0, min(alpha, 1.0)))))

    def clip(self, path): self.cv.clipPath(self._native(path), True)

    # ── drawing ──────────────────────────────────────────────────────────────
    def _native(self, path: Path):
        base = path.native
        if base is None:
            base = self._paths.get(path.cmds)
            if base is None:
                base = self.sk.Path()
                for c in path.cmds:
                    if c[0] == "M":
                        base.moveTo(c[1], c[2])
                    elif c[0] == "L":
                        base.lineTo(c[1], c[2])
                    elif c[0] == "C":
                        base.cubicTo(*c[1:])
                    else:
                        base.close()
                if len(self._paths) > 4000:
                    self._paths.clear()
                self._paths[path.cmds] = base
        if path.dx or path.dy:
            base = self.sk.Path(base)
            base.offset(path.dx, path.dy)
        return base

    def _paint(self, color: str, alpha: float = 1.0):
        return self.sk.Paint(AntiAlias=True, Color=self.sk.Color(*_rgba(color, alpha)))

    def fill(self, path, color, alpha=1.0):
        self.cv.drawPath(self._native(path), self._paint(color, alpha))

    def stroke(self, path, color, width, alpha=1.0, cap="round", join="round"):
        sk, p = self.sk, self._paint(color, alpha)
        p.setStyle(sk.Paint.kStroke_Style)
        p.setStrokeWidth(width)
        p.setStrokeCap(sk.Paint.kButt_Cap if cap == "butt" else sk.Paint.kRound_Cap)
        p.setStrokeJoin(sk.Paint.kMiter_Join if join == "miter" else sk.Paint.kRound_Join)
        self.cv.drawPath(self._native(path), p)

    def halftone(self, path, color, cell, angle, radius, alpha=1.0):
        sk = self.sk
        size = max(int(round(cell)), 2)
        key = (color, size, angle, round(radius, 2), round(alpha, 3))
        if key not in self._tiles:
            s = sk.Surface(size, size)
            c = s.getCanvas()
            c.clear(sk.ColorTRANSPARENT)
            c.drawCircle(size / 2, size / 2, radius, self._paint(color, alpha))
            m = sk.Matrix()
            m.setRotate(angle)
            self._tiles[key] = s.makeImageSnapshot().makeShader(sk.TileMode.kRepeat, sk.TileMode.kRepeat,
                                                                 sk.SamplingOptions(sk.FilterMode.kLinear), m)
        p = sk.Paint(AntiAlias=True)
        p.setShader(self._tiles[key])
        self.cv.drawPath(self._native(path), p)

    def text(self, text, x, y, font, size, color, alpha=1.0):
        self.cv.drawString(text, x, y, self.fonts.font(font, size), self._paint(color, alpha))

    def text_path(self, text, x, y, font, size) -> Path:
        key = (text, font, round(size * 4) / 4)
        base = self._text_paths.get(key)
        if base is None:
            f = self.fonts.font(font, size)
            glyphs = f.textToGlyphs(text)
            base = self.sk.Path()
            for g, pos in zip(glyphs, f.getPos(glyphs)):
                gp = f.getPath(g)
                if gp is not None:
                    gp.offset(pos.x(), pos.y())
                    base.addPath(gp)
            if len(self._text_paths) > 300:
                self._text_paths.clear()
            self._text_paths[key] = base
        return Path(native=base, dx=x, dy=y)

    def measure(self, text, font, size) -> float:
        return self.fonts.measure(text, font, size)

    def _fit(self, img, x, y, w, h):
        k = min(w / img.width(), h / img.height())
        dw, dh = img.width() * k, img.height() * k
        return self.sk.Rect.MakeXYWH(x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)

    def _image(self, file: str):
        img = self.images.get(file)
        if img is None:
            raise UserError(f"Graphic: image not found or not readable: {file}")
        return img

    def image(self, file, x, y, w, h, alpha=1.0):
        img = self._image(file)
        p = self.sk.Paint(AntiAlias=True)
        p.setAlphaf(max(0.0, min(alpha, 1.0)))
        self.cv.drawImageRect(img, self._fit(img, x, y, w, h), self._sampling, p)

    def sticker(self, file, x, y, w, h, border, border_color, outline, outline_color):
        sk, img = self.sk, self._image(file)
        dst = self._fit(img, x + border + outline, y + border + outline, w - 2 * (border + outline), h - 2 * (border + outline))
        if img.isOpaque():                       # no silhouette: a framed photo
            if border > 0:
                frame = sk.Rect.MakeLTRB(dst.left() - border, dst.top() - border, dst.right() + border, dst.bottom() + border)
                self.cv.drawRect(frame, self._paint(border_color))
                if outline > 0:
                    p = self._paint(outline_color)
                    p.setStyle(sk.Paint.kStroke_Style)
                    p.setStrokeWidth(outline)
                    p.setStrokeJoin(sk.Paint.kRound_Join)
                    self.cv.drawRect(frame, p)
        elif border > 0:                         # a cut-out: the border follows its silhouette
            for radius, color in ((border + outline, outline_color), (border, border_color)):
                if radius > 0 and (color != outline_color or outline > 0):
                    p = sk.Paint(AntiAlias=True)
                    p.setImageFilter(sk.ImageFilters.ColorFilter(
                        sk.ColorFilters.Blend(sk.Color(*_rgba(color)), sk.BlendMode.kSrcIn),
                        sk.ImageFilters.Dilate(radius, radius)))
                    self.cv.drawImageRect(img, dst, self._sampling, p)
        self.cv.drawImageRect(img, dst, self._sampling, sk.Paint(AntiAlias=True))


class SkiaGraphicRenderer:
    """GraphicRenderer port: stage (with real text measuring), frames and stills of a scene."""

    def __init__(self):
        self._skia = None
        self._books: dict = {}
        self._images = None
        self._grain: dict = {}

    def ready(self) -> str | None:
        """None when graphics can be drawn; otherwise why not."""
        return None if importlib.util.find_spec("skia") is not None else MISSING

    def _sk(self):
        if self._skia is None:
            if self.ready():
                raise UserError(f"Graphics need Skia: {MISSING}")
            import skia

            self._skia = skia
            self._images = ImageStore(skia)
        return self._skia

    def _book(self, theme: dict) -> FontBook:
        key = repr(sorted((theme.get("fonts") or {}).items(), key=lambda kv: kv[0]))
        if key not in self._books:
            self._books[key] = FontBook(self._sk(), theme.get("fonts") or {})
        return self._books[key]

    def stage(self, W: int, H: int, theme: dict, language: str = "es") -> Stage:
        book = self._book(theme)
        return Stage(W, H, theme, measure=book.measure, language=language, image_size=self._images.size)

    def _canvas(self, st: Stage) -> SkiaCanvas:
        return SkiaCanvas(self._sk(), st.W, st.H, self._book(st.theme), self._images)

    def _grain_fields(self, st: Stage) -> list[np.ndarray] | None:
        """A few fields of paper grain (signed, added to the picture), cached per size and strength."""
        strength = float(st.theme["grain"].get("strength", 0))
        if strength <= 0:
            return None
        key = (st.W, st.H, strength)
        if key not in self._grain:
            fields = []
            for k in range(GRAIN_PHASES):
                rng = np.random.default_rng(1000 + k)
                fine = rng.normal(0, 1, (st.H, st.W)).astype(np.float32)
                small = rng.normal(0, 1, (st.H // 6 + 1, st.W // 6 + 1)).astype(np.float32)
                blot = np.kron(small, np.ones((6, 6), np.float32))[:st.H, :st.W]
                fields.append(np.rint((fine * 0.6 + blot * 0.4) * 38 * strength).astype(np.int16)[:, :, None])
            self._grain = {key: fields}
        return self._grain[key]

    def _states(self, scene: Scene, fps: int, n: int, opaque: bool) -> Iterator[tuple[np.ndarray, float, tuple]]:
        """(picture, t, state key) per frame; each distinct state of the scene is drawn once."""
        st = scene.st
        cv = self._canvas(st)
        settled: dict = {}
        last_key, last = None, None
        for i in range(n):
            t = i / fps
            key, td = scene.frame_state(t)
            body = settled.get(key) if key[0] == "settled" else (last if key == last_key else None)
            if body is None:
                cv.begin()
                if opaque and scene.overlay:
                    kit.scenery(cv, st, td)
                scene.paint(cv, td)
                body = cv.rgb() if opaque else cv.rgba()
                if key[0] == "settled":
                    settled[key] = body
                else:
                    last_key, last = key, body
            yield body, t, key

    def frames(self, scene: Scene, fps: int, n: int) -> Iterator[bytes]:
        """n raw RGB frames (rgb24) of the scene on its stage, with paper grain when the theme has it."""
        st = scene.st
        grain = self._grain_fields(st)
        gfps = float(st.theme["grain"].get("fps", 15))
        done: dict = {}                      # settled states with their grain already added
        for body, t, state in self._states(scene, fps, n, opaque=True):
            if grain is None:
                yield body.tobytes()
                continue
            phase = int(t * gfps + 1e-6) % GRAIN_PHASES
            data = done.get((state, phase)) if state[0] == "settled" else None
            if data is None:
                data = np.clip(body.astype(np.int16) + grain[phase], 0, 255).astype(np.uint8).tobytes()
                if state[0] == "settled":
                    done[(state, phase)] = data
            yield data

    def overlay_frames(self, scene: Scene, fps: int, n: int) -> Iterator[np.ndarray]:
        """n RGBA pictures (H×W×4, straight alpha) of the scene on a transparent background."""
        for body, _, _ in self._states(scene, fps, n, opaque=False):
            yield body

    def still(self, scene: Scene, t: float) -> np.ndarray:
        """The frame at `t` as an RGB array (H×W×3)."""
        st = scene.st
        cv = self._canvas(st)
        cv.begin()
        if scene.overlay:
            kit.scenery(cv, st, t)
        scene.paint(cv, scene.frame_state(t)[1])
        body = cv.rgb()
        grain = self._grain_fields(st)
        if grain is not None:
            body = np.clip(body.astype(np.int16) + grain[0], 0, 255).astype(np.uint8)
        return body

    # ── files ────────────────────────────────────────────────────────────────
    def save_picture(self, picture: np.ndarray, out: FilePath) -> FilePath:
        from PIL import Image

        out = FilePath(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(picture).save(out)
        return out

    def save_sheet(self, pictures: list[np.ndarray], out: FilePath, columns: int, tile_width: int) -> FilePath:
        from PIL import Image

        gap = 6
        tiles = []
        for p in pictures:
            img = Image.fromarray(p)
            tiles.append(img.resize((tile_width, round(img.height * tile_width / img.width)), Image.LANCZOS))
        th = max(t.height for t in tiles)
        rows = -(-len(tiles) // columns)
        sheet = Image.new("RGB", (columns * (tile_width + gap) - gap, rows * (th + gap) - gap), (70, 70, 70))
        for i, t in enumerate(tiles):
            sheet.paste(t, ((i % columns) * (tile_width + gap), (i // columns) * (th + gap)))
        out = FilePath(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out)
        return out

    def sample_image(self, out: FilePath) -> FilePath:
        """A made-up gadget on a transparent background (a stand-in product photo for the style proof)."""
        sk = self._sk()
        s = sk.Surface(900, 560)
        c = s.getCanvas()
        c.clear(sk.ColorTRANSPARENT)
        c.rotate(-8, 450, 280)

        def box(x, y, w, h, r, color):
            c.drawRoundRect(sk.Rect.MakeXYWH(x, y, w, h), r, r, sk.Paint(AntiAlias=True, Color=sk.Color(*_rgba(color))))

        box(90, 150, 720, 250, 26, "#1D1F26")
        box(110, 170, 680, 210, 16, "#2C2F3A")
        for k in range(4):
            box(140 + k * 118, 205, 92, 140, 10, "#111318")
            box(152 + k * 118, 220, 68, 20, 4, "#3A3E4C")
        box(630, 205, 130, 140, 10, "#D9C9A3")
        for k in range(9):
            box(70, 172 + k * 23, 26, 12, 3, "#C9A24B")
        out = FilePath(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        s.makeImageSnapshot().save(str(out), sk.kPNG)
        return out
