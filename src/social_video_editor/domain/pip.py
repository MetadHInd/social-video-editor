"""Picture-in-picture: while a full-frame cutaway (a graphic, a photo) is on screen, the presenter
stays in a small window in a corner instead of disappearing.

Pure geometry: where the window goes, which part of the main picture is shown in it (around the
face when it is known) and how much of the frame is left for the graphic, which is then laid out in
that smaller safe zone so nothing ends up under the window.
"""
from __future__ import annotations

from .graphics.scene import SAFE_LANDSCAPE, SAFE_VERTICAL
from .models import UserError

DEFAULTS = {
    "enabled": False,
    "corner": "bottom_right",   # bottom_right | bottom_left | top_right | top_left
    "size": 0.32,               # window width as a share of the frame's short side
    "min_seconds": 2.5,         # a cutaway shorter than this takes the whole frame
}
CORNERS = ("bottom_right", "bottom_left", "top_right", "top_left")
ASPECT = 1.22                   # window height / width (a portrait card)
BORDER = 18 / 1080              # the frame's paper border (graphics/overlays.py PipFrame), share of the short side
GAP = 0.03                      # air between the window and the graphic, share of the short side
FACE_HEIGHTS = 2.8              # the window shows this many face heights (head and shoulders)
EYES_AT = 0.36                  # the eyes sit at this share of the window's height
NO_FACE = {True: (0.5, 0.33, 0.52), False: (0.5, 0.42, 0.9)}   # vertical / landscape: center x, center y, height shown


def settings(cfg: dict) -> dict:
    p = {**DEFAULTS, **{k: v for k, v in (cfg.get("pip") or {}).items() if v is not None}}
    if p["corner"] not in CORNERS:
        raise UserError(f"pip.corner must be one of {', '.join(CORNERS)}; got {p['corner']!r}")
    return p


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def layout(W: int, H: int, cfg: dict, face: dict | None = None, safe: tuple | None = None) -> dict:
    """Where everything goes in a W×H frame:

    {"rect": (x, y, w, h) of the window in pixels, "crop": (x, y, w, h) of the main picture shown in
    it, "frame": (x, y, w, h) of the window as shares of the frame (the pip_frame graphic's `rect`),
    "safe": (x0, y0, x1, y1) shares of the frame left for the graphic}.

    face: where the presenter's face is IN THE FRAME ({"x", "y", "size", "eyes"}, shares) or None.
    safe: the graphics' safe zone as shares (default: the engine's)."""
    p = settings(cfg)
    vertical = H > W
    short = min(W, H)
    x0, y0, x1, y1 = safe or (SAFE_VERTICAL if vertical else SAFE_LANDSCAPE)
    border, gap = BORDER * short, GAP * short
    w = _even(float(p["size"]) * short)
    h = _even(w * ASPECT)
    right, bottom = "right" in p["corner"], "bottom" in p["corner"]
    x = _even(x1 * W - border - w) if right else _even(x0 * W + border)
    y = _even(y1 * H - border - h) if bottom else _even(y0 * H + border)
    left_for_graphic = [x0, y0, x1, y1]
    if vertical:                                  # the graphic keeps the full width, above or below the window
        if bottom:
            left_for_graphic[3] = (y - border - gap) / H
        else:
            left_for_graphic[1] = (y + h + border + gap) / H
    elif right:                                   # landscape: the graphic keeps the full height, beside the window
        left_for_graphic[2] = (x - border - gap) / W
    else:
        left_for_graphic[0] = (x + w + border + gap) / W
    return {"rect": (x, y, w, h), "crop": crop(W, H, w / h, face),
            "frame": (round(x / W, 5), round(y / H, 5), round(w / W, 5), round(h / H, 5)),
            "safe": tuple(round(v, 4) for v in left_for_graphic)}


def crop(W: int, H: int, aspect: float, face: dict | None = None) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the main picture shown in a window of `aspect` (width / height): head and
    shoulders around the face, or the upper middle of the frame when no face is known."""
    if face and face.get("size"):
        ch = min(max(float(face["size"]) * FACE_HEIGHTS, 0.28), 1.0) * H
        cx = float(face["x"]) * W
        eyes = float(face.get("eyes", face["y"]))
        top = eyes * H - EYES_AT * ch
    else:
        fx, fy, share = NO_FACE[H > W]
        ch, cx = share * H, fx * W
        top = fy * H - ch / 2
    cw = ch * aspect
    if cw > W:
        cw, ch = float(W), W / aspect
    cw, ch = min(_even(cw), W // 2 * 2), min(_even(ch), H // 2 * 2)
    x = int(min(max(cx - cw / 2, 0), W - cw))
    y = int(min(max(top, 0), H - ch))
    return x, y, cw, ch


def face_in_frame(face: dict | None, src_w: float, src_h: float, W: int, H: int, focus: tuple[float, float],
                  mode: str = "auto", min_ratio: float = 0.5) -> dict | None:
    """Where a face found in the source ({"x", "y", "size", "eyes"} as shares of the SOURCE picture)
    ends up in the W×H frame, given how the shot is framed (focus_x / focus_y, framing mode).
    None when the face is not known well enough."""
    if not face or float(face.get("ratio", 1)) < min_ratio or "x" not in face:
        return None
    fx, fy = focus
    r_src, r_out = src_w / src_h, W / H
    blur = mode == "blur" or (mode == "auto" and max(r_src, r_out) / min(r_src, r_out) > 1.3)
    scale = (min if blur else max)(W / src_w, H / src_h)
    pw, ph = src_w * scale, src_h * scale                       # the source picture, scaled into the frame
    off_x = (W - pw) / 2 if blur else -(pw - W) * fx            # where its left / top edge falls
    off_y = (H - ph) / 2 if blur else -(ph - H) * fy
    eyes = float(face.get("eyes", face["y"]))
    return {"x": (off_x + float(face["x"]) * pw) / W, "y": (off_y + float(face["y"]) * ph) / H,
            "eyes": (off_y + eyes * ph) / H, "size": float(face.get("size", 0)) * ph / H}
