"""ImageComposer with Pillow: frames of drawn shots (polaroid, collage) and contact sheets (of video
frames, or of the still photos themselves)."""
from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from ...domain.timebase import mmss
from ..system.fonts import label_font
from . import rich_text as rt
from .drawing import center_crop, drop_shadow, ease_out, rounded, soft_background, to_rgb_bytes
from .fonts import load_font


POLAROID_HEIGHT_RATIO = 1.25 + 0.06 + 0.27   # frame height / photo width (4:5 photo, margin, caption strip)
POLAROID_MAX_HEIGHT = 0.82                   # the frame never takes more than this share of the height
LANDSCAPE_COLLAGE = {"y0": 0.10, "y1": 0.90, "width": 0.90}   # title-safe zone of a landscape grid


def polaroid_photo_width(W: int, H: int, cfg: dict) -> int:
    """Width of the polaroid photo: polaroid.width·W, bounded so the whole frame fits the height.

    In vertical output the width bound always wins (unchanged); in landscape the height does.
    """
    p = cfg.get("polaroid", {})
    return int(min(W * float(p.get("width", 0.70)), H * POLAROID_MAX_HEIGHT / POLAROID_HEIGHT_RATIO))


def _grid_cells(n: int, cols: int, W: int, H: int, zone_y0: float, zone_y1: float, width: float,
                gap: int) -> tuple[int, int, int, int]:
    """3:4 cells of a cols-wide grid fitting width·W and the vertical zone → (rows, cw, ch, total_h)."""
    rows = (n + cols - 1) // cols
    cw = int((W * width - gap * (cols - 1)) / cols)
    ch = int(cw * 4 / 3)
    total_h = rows * ch + (rows - 1) * gap
    if total_h > H * (zone_y1 - zone_y0):              # must fit in the safe zone
        ch = int((H * (zone_y1 - zone_y0) - gap * (rows - 1)) / rows)
        cw = int(ch * 3 / 4)
        total_h = rows * ch + (rows - 1) * gap
    return rows, cw, ch, total_h


def collage_layout(n: int, W: int, H: int, shot: dict, cfg: dict) -> dict:
    """Grid geometry of a collage: columns, rows, cell size and top-left corner of the grid.

    Portrait / square: `columns` (shot, else collage.columns) inside the collage.y0–y1 zone.
    Landscape (W > H): the column count that gives the largest cells inside a title-safe zone
    (ties → fewest empty slots, then more columns / fewer rows); a portrait column count would
    make tiny cells.
    """
    c = cfg.get("collage", {})
    gap = int(min(W, H) * 0.022)
    if W > H:
        zone_y0, zone_y1, width = LANDSCAPE_COLLAGE["y0"], LANDSCAPE_COLLAGE["y1"], LANDSCAPE_COLLAGE["width"]
        n_ = max(n, 1)
        options = []
        for k in range(1, n_ + 1):
            rows, cw, ch, total_h = _grid_cells(n_, k, W, H, zone_y0, zone_y1, width, gap)
            options.append(((cw, -(rows * k - n_), k), (k, rows, cw, ch, total_h)))
        cols, rows, cw, ch, total_h = max(options)[1]
    else:
        zone_y0, zone_y1, width = float(c.get("y0", 0.215)), float(c.get("y1", 0.72)), 0.94
        cols = int(shot.get("columns", c.get("columns", 4)))
        rows, cw, ch, total_h = _grid_cells(n, cols, W, H, zone_y0, zone_y1, width, gap)
    x0 = (W - (cols * cw + (cols - 1) * gap)) / 2
    y0 = H * (zone_y0 + zone_y1) / 2 - total_h / 2
    return {"columns": cols, "rows": rows, "cw": cw, "ch": ch, "gap": gap, "x0": x0, "y0": y0}


def _label_font(size: int):
    path = label_font()
    try:
        if path is not None:
            return ImageFont.truetype(str(path), size)
    except OSError:
        pass
    return ImageFont.load_default()


class PillowComposer:
    # ── polaroid ─────────────────────────────────────────────────────────────
    def _photo(self, frame: Image.Image, W: int, text: str, cfg: dict, H: int | None = None) -> Image.Image:
        p = cfg.get("polaroid", {})
        photo_w = polaroid_photo_width(W, H or W * 16 // 9, cfg)
        photo = center_crop(frame, 4 / 5, float(p.get("center_y", 0.5))).resize(
            (photo_w, int(photo_w * 5 / 4)), Image.LANCZOS)
        m, bottom = int(photo_w * 0.06), int(photo_w * 0.27)
        frame_im = Image.new("RGBA", (photo_w + 2 * m, photo.height + m + bottom), (252, 250, 246, 255))
        frame_im.paste(photo, (m, m))
        if text:
            size = int(photo_w * 0.085)
            f = load_font(p.get("font", "Bradley Hand Bold"), size)
            lines = rt.wrap(text, f, size, photo_w)
            y = photo.height + m + (bottom - size * 1.2 * len(lines)) / 2
            for ln in lines:
                x = (frame_im.width - rt.width(ln, f, size)) / 2
                rt.draw(frame_im, x, y, ln, f, size, (60, 52, 58, 255))
                y += size * 1.2
        ImageDraw.Draw(frame_im).rectangle([0, 0, frame_im.width - 1, frame_im.height - 1],
                                           outline=(225, 220, 215, 255))
        return drop_shadow(frame_im, radius=22, opacity=150, offset=18)

    def polaroid_frames(self, frame_png: Path, shot: dict, W: int, H: int, fps: int, cfg: dict) -> Iterator[bytes]:
        """The photo drops in tilting with a white flash, then slowly comes closer."""
        frame = Image.open(frame_png).convert("RGB")
        background = soft_background(frame, W, H)
        photo = self._photo(frame, W, shot.get("text", ""), cfg, H)
        tilt = float(shot.get("tilt", -4))
        n = int(round(float(shot["dur"]) * fps))
        white = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        drop = float(cfg.get("polaroid", {}).get("drop_time", 0.16))      # seconds the photo takes to land
        flash = float(cfg.get("polaroid", {}).get("flash", 0.10))
        for i in range(n):
            t = i / fps
            e = ease_out(t / drop)
            sc = 1.35 - 0.35 * e + 0.035 * max(t - drop, 0) / max(n / fps, 0.1)
            ang = tilt - 10 * (1 - e)
            im = photo.resize((int(photo.width * sc), int(photo.height * sc)), Image.BICUBIC)
            im = im.rotate(ang, resample=Image.BICUBIC, expand=True)
            c = background.copy()
            c.alpha_composite(im, (int((W - im.width) / 2), int(H * 0.47 - im.height / 2)))
            fl = max(0.0, 1 - t / flash)                      # flash when shooting
            if fl > 0:
                c = Image.blend(c, white, 0.85 * fl)
            yield to_rgb_bytes(c)

    # ── collage ──────────────────────────────────────────────────────────────
    def collage_frames(self, frame_pngs: list[Path], background_png: Path, shot: dict, times: list[float],
                       W: int, H: int, fps: int, cfg: dict) -> Iterator[bytes]:
        """Grid of photos popping in one by one at `times` (seconds inside the shot)."""
        g = collage_layout(len(frame_pngs), W, H, shot, cfg)
        cols, cw, ch, gap, x0, y0 = g["columns"], g["cw"], g["ch"], g["gap"], g["x0"], g["y0"]
        rnd = random.Random(3)
        cells = []
        for k, png in enumerate(frame_pngs):
            im = center_crop(Image.open(png).convert("RGB"), 3 / 4).resize((cw - 12, ch - 12), Image.LANCZOS)
            card = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
            card.paste(im, (6, 6))
            cell = drop_shadow(rounded(card, int(cw * 0.07)), radius=10, opacity=120, offset=8)
            cx = x0 + (k % cols) * (cw + gap) + cw / 2
            cy = y0 + (k // cols) * (ch + gap) + ch / 2
            cells.append((cell, cx, cy, rnd.uniform(-5, 5)))
        background = soft_background(Image.open(background_png).convert("RGB"), W, H, dark=0.62)
        n = int(round(float(shot["dur"]) * fps))
        turned = {}
        for i in range(n):
            t = i / fps
            canvas = background.copy()
            for k, (cell, cx, cy, ang) in enumerate(cells):
                dt = t - times[k]
                if dt < 0:
                    continue
                sc = round(0.55 + 0.6 * ease_out(dt / 0.07) - 0.15 * ease_out((dt - 0.07) / 0.09), 2)
                key = (k, sc)
                if key not in turned:
                    im = cell.resize((max(int(cell.width * sc), 1), max(int(cell.height * sc), 1)), Image.BICUBIC)
                    turned[key] = im.rotate(ang, resample=Image.BICUBIC, expand=True)
                im = turned[key]
                canvas.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))
            yield to_rgb_bytes(canvas)

    # ── contact sheets ───────────────────────────────────────────────────────
    def contact_sheets(self, info: dict, analysis: dict, read_frame: Callable, out_dir: Path, start: float = 0,
                       end: float | None = None, every: float | None = None, columns: int = 6, rows: int = 5,
                       thumb_w: int = 170) -> list[Path]:
        """Grids of frames with their time, a motion bar (green) and a loudness bar (orange)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        end = min(end or info["duration"], info["duration"])
        if every is None:  # the whole video in ~4 sheets
            every = max(1.0, round((end - start) / (columns * rows * 4)))
        times = list(np.arange(start, end, every))
        motion = [m["motion"] for m in analysis["samples"]] or [1]
        motion_max = np.percentile(motion, 95) or 1
        audio = analysis.get("audio")
        thumb_h = int(thumb_w * info["height"] / info["width"])
        f_label = _label_font(15)
        paths, per_sheet = [], columns * rows
        suffix = f"_{int(start)}-{int(end)}s" if (start or end < info["duration"]) else ""
        ts = np.array([m["t"] for m in analysis["samples"]]) if analysis["samples"] else None
        for h in range(0, len(times), per_sheet):
            batch = times[h:h + per_sheet]
            sheet = Image.new("RGB", (columns * (thumb_w + 6) + 6, rows * (thumb_h + 30) + 6), (20, 20, 24))
            d = ImageDraw.Draw(sheet)
            for k, t in enumerate(batch):
                rgb = read_frame(t)
                if rgb is None:
                    continue
                img = Image.fromarray(rgb).resize((thumb_w, thumb_h))
                x = 6 + (k % columns) * (thumb_w + 6)
                y = 6 + (k // columns) * (thumb_h + 30)
                sheet.paste(img, (x, y))
                d.text((x + 2, y + thumb_h + 2), mmss(t), font=f_label, fill=(255, 255, 255))
                mv = 0.0
                if ts is not None:
                    mv = float(analysis["samples"][int(np.clip(np.searchsorted(ts, t), 0, len(ts) - 1))]["motion"])
                m = min(mv / motion_max, 1)
                d.rectangle([x, y + thumb_h + 21, x + int(thumb_w * m), y + thumb_h + 24], fill=(80, 220, 120))
                if audio:
                    i = min(int(t / audio["step"]), len(audio["db"]) - 1)
                    v = np.clip((audio["db"][i] + 60) / 60, 0, 1)
                    d.rectangle([x, y + thumb_h + 25, x + int(thumb_w * v), y + thumb_h + 28], fill=(250, 150, 50))
            path = out_dir / f"{info['id']}{suffix}_sheet_{h // per_sheet + 1:02d}.jpg"
            sheet.save(path, quality=88)
            paths.append(path)
        return paths

    def photo_sheets(self, photos: list[dict], out_dir: Path, name: str = "photos", columns: int = 6,
                     rows: int = 5, thumb_w: int = 170) -> list[Path]:
        """Grids of the photos themselves (EXIF orientation applied, fitted in 4:3 cells), labeled with
        their source index and capture date (or file name). photos: media infos (+ "index").
        An unreadable photo leaves a dark cell with its label."""
        out_dir.mkdir(parents=True, exist_ok=True)
        thumb_h = int(thumb_w * 3 / 4)
        f_label = _label_font(13)
        paths, per_sheet = [], columns * rows
        for h in range(0, len(photos), per_sheet):
            batch = photos[h:h + per_sheet]
            sheet = Image.new("RGB", (columns * (thumb_w + 6) + 6, rows * (thumb_h + 22) + 6), (20, 20, 24))
            d = ImageDraw.Draw(sheet)
            for k, info in enumerate(batch):
                x = 6 + (k % columns) * (thumb_w + 6)
                y = 6 + (k // columns) * (thumb_h + 22)
                d.rectangle([x, y, x + thumb_w - 1, y + thumb_h - 1], fill=(36, 36, 42))
                try:
                    with Image.open(info["path"]) as im:
                        im.draft("RGB", (thumb_w * 2, thumb_h * 2))
                        im = ImageOps.exif_transpose(im).convert("RGB")
                        im.thumbnail((thumb_w, thumb_h), Image.LANCZOS)
                        sheet.paste(im, (x + (thumb_w - im.width) // 2, y + (thumb_h - im.height) // 2))
                except (OSError, ValueError):
                    pass
                taken = str(info.get("taken") or "")[:10]
                label = f"{info.get('index', h + k)} {taken or Path(str(info['path'])).name}"
                d.text((x + 2, y + thumb_h + 3), label[:26], font=f_label, fill=(255, 255, 255))
            path = out_dir / f"{name}_sheet_{h // per_sheet + 1:02d}.jpg"
            sheet.save(path, quality=88)
            paths.append(path)
        return paths
