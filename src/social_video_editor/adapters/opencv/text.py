"""Where printed text is in a picture (titles, captions, labels on a slide), without a model: the classic
morphology approach — strong local contrast (morphological gradient), joined along rows into word
and line shapes, kept when they look like lines of letters (wide, short, partly filled). Used to keep
b-roll free of other people's titles and captions. It finds overlaid text well; text inside a scene
(a sign on a wall) only when it is large and sharp."""
from __future__ import annotations

import numpy as np

WIDTH = 640                 # frames are looked at this wide
CHAR_HEIGHT = (6, 0.2)      # a letter (or a word whose letters touch): pixels at WIDTH … share of the frame height
CHAR_FILL = (0.12, 0.95)    # share of a letter's box covered by its strokes
MIN_HEIGHT = 8              # pixels (at WIDTH) of a line of text
MAX_HEIGHT = 0.2            # share of the frame height
MIN_WIDTH = 0.06            # share of the frame width
MIN_ASPECT = 2.0            # a line is at least this many times wider than tall
MIN_PIECES = 2              # letters (or words) in a line…
WORD_ASPECT = 3.0           # …or one word whose letters touch: this many times wider than tall,
WORD_FILL = (0.2, 0.65)     # with strokes covering this share of its box (an outline, not a solid bar)
BUSY = 0.5                  # a frame whose contrast mask covers more than this is texture, not text
MAX_MIDDLE = 0.16           # share of middle greys in a line of printed text (ink and background only)
MIN_CONTRAST = 90           # grey levels between the ink and the background
MIN_STROKES = 1.2           # strokes crossed per line height along the middle of a line


def text_boxes(frame) -> list[tuple[float, float, float, float]]:
    """Boxes (x, y, w, h, as shares of the frame) of the lines of text in a BGR frame.

    Shapes the size of letters are kept (long lines, outlines and big objects are dropped, so a
    label touching a drawing is still found), then joined along rows into lines."""
    import cv2

    h0, w0 = frame.shape[:2]
    k = WIDTH / max(w0, h0)
    small = cv2.resize(frame, (max(int(w0 * k), 1), max(int(h0 * k), 1)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if bw.mean() / 255 > BUSY:
        return []
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw)
    keep = np.zeros(n, bool)
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if CHAR_HEIGHT[0] <= h <= CHAR_HEIGHT[1] * H and w <= 0.5 * W and CHAR_FILL[0] < area / (w * h) < CHAR_FILL[1]:
            keep[i] = True
    letters = np.where(keep[labels], 255, 0).astype(np.uint8)
    joined = cv2.morphologyEx(letters, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(W // 45, 5), 1)))
    m, lab2, stats2, _ = cv2.connectedComponentsWithStats(joined)
    out = []
    for i in range(1, m):
        x, y, w, h, _ = (int(v) for v in stats2[i])
        if h < MIN_HEIGHT or h > MAX_HEIGHT * H or w < MIN_ASPECT * h or w < MIN_WIDTH * W:
            continue
        inside = np.unique(labels[y:y + h, x:x + w][(lab2[y:y + h, x:x + w] == i) & (letters[y:y + h, x:x + w] > 0)])
        if not (len(inside) >= MIN_PIECES or (len(inside) >= 1 and w >= WORD_ASPECT * h
                and WORD_FILL[0] < np.count_nonzero(letters[y:y + h, x:x + w]) / (w * h) < WORD_FILL[1])):
            continue
        if _printed(gray[y:y + h, x:x + w], bw[y:y + h, x:x + w]):
            out.append((round(x / W, 4), round(y / H, 4), round(w / W, 4), round(h / H, 4)))
    return out


def _printed(gray, strokes) -> bool:
    """Printed text is two-tone (ink on a background: few middle greys, strong contrast) and has many
    strokes across a line; rows of app icons, fabric or foliage have many middle tones or few strokes."""
    g = gray.astype(np.float32).ravel()
    lo, hi = np.percentile(g, 10), np.percentile(g, 90)
    if hi - lo < MIN_CONTRAST:
        return False
    middle = float(np.mean((g > lo + 0.25 * (hi - lo)) & (g < hi - 0.25 * (hi - lo))))
    h, w = strokes.shape
    rows = strokes[int(h * 0.2):max(int(h * 0.8), int(h * 0.2) + 1)] > 0
    crossings = float(np.mean([np.count_nonzero(np.diff(r.astype(np.int8)) == 1) for r in rows])) if len(rows) else 0.0
    return middle <= MAX_MIDDLE and crossings / max(w / max(h, 1), 1e-6) >= MIN_STROKES
