"""Finding the presenter's face in a video with what OpenCV offers in the installed version.

- OpenCV 4.x ships the Haar cascades (cv2.CascadeClassifier + cv2.data.haarcascades).
- OpenCV 5 moved them out of the main package; it has cv2.FaceDetectorYN (YuNet), which needs a small
  model file (≈230 KB, MIT license, from the OpenCV model zoo). It is looked up in the user cache
  (<cache>/models/) and downloaded there only when asked to (framing.face: true).

Both give face boxes; YuNet also gives the eyes. Positions are returned as shares of the picture.
"""
from __future__ import annotations

import urllib.request
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from ...log import log
from ..files.atomic import publish, temp_name
from ..system.tools import user_cache_dir
from .capture import open_capture

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
YUNET_URL = f"https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/{YUNET_FILE}"
YUNET_MIN_BYTES = 100_000          # a failed download (an error page, a pointer file) is smaller
YUNET_SCORE = 0.7
MIN_FACE = 0.06                    # faces smaller than this share of the picture height are ignored
EYES_ABOVE_CENTER = 0.12           # Haar gives no eyes: this share of the face height above its middle
DOWNLOAD_TIMEOUT = 30


def yunet_path() -> Path:
    return user_cache_dir() / "models" / YUNET_FILE


def _download_yunet(dest: Path) -> bool:
    log(f"Face framing: downloading the face detector ({YUNET_FILE}, ≈230 KB, once) to {dest.parent}…")
    tmp = temp_name(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(YUNET_URL, timeout=DOWNLOAD_TIMEOUT) as r:     # noqa: S310 (fixed https URL)
            data = r.read()
        if len(data) < YUNET_MIN_BYTES:
            raise OSError(f"unexpected file ({len(data)} bytes)")
        tmp.write_bytes(data)
        publish(tmp, dest)
        return True
    except (OSError, ValueError) as e:
        log(f"Warning: could not download the face detector ({e}); shots are framed without it.")
        return False
    finally:
        tmp.unlink(missing_ok=True)


def detector_state() -> tuple[str, bool] | None:
    """(detector name, ready to use) for this OpenCV without loading anything: ("haar", True),
    ("yunet", whether its model is in the user cache), or None when OpenCV has no face detector."""
    import cv2

    if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
        if (Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml").is_file():
            return "haar", True
    if hasattr(cv2, "FaceDetectorYN"):
        return "yunet", yunet_path().is_file()
    return None


@contextmanager
def _quiet(cv2):
    """OpenCV 5 warns on every model load that its new engine ignores the (default) target: not our news."""
    logging = getattr(getattr(cv2, "utils", None), "logging", None)
    if logging is None:
        yield
        return
    level = logging.getLogLevel()
    logging.setLogLevel(logging.LOG_LEVEL_ERROR)
    try:
        yield
    finally:
        logging.setLogLevel(level)


def make_detector(download: bool = False):
    """(name, detect) where detect(BGR frame) → [(x, y, w, h, eyes_y | None)] in pixels; None when this
    OpenCV has no usable face detector."""
    import cv2

    if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
        xml = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(str(xml)) if xml.is_file() else None
        if cascade is not None and not cascade.empty():
            def haar(frame):
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                side = max(int(frame.shape[0] * MIN_FACE), 24)
                return [(x, y, w, h, None) for x, y, w, h in cascade.detectMultiScale(gray, 1.1, 5, 0, (side, side))]
            return "haar", haar
    if hasattr(cv2, "FaceDetectorYN"):
        model = yunet_path()
        if not model.is_file() and not (download and _download_yunet(model)):
            return None
        try:
            with _quiet(cv2):
                net = cv2.FaceDetectorYN.create(str(model), "", (320, 320), YUNET_SCORE, 0.3, 50)
        except cv2.error as e:
            log(f"Warning: the face detector could not be loaded ({e}); shots are framed without it.")
            return None

        def yunet(frame):
            net.setInputSize((frame.shape[1], frame.shape[0]))
            _, faces = net.detect(frame)
            return [(f[0], f[1], f[2], f[3], (f[5] + f[7]) / 2) for f in (faces if faces is not None else [])]
        return "yunet", yunet
    return None


def find_face(video: str, per_second: float = 1.0, download: bool = False) -> dict | None:
    """The biggest face of one frame per second → {"x", "y", "size", "eyes", "ratio", "frames",
    "detector"} (medians, 0–1 of the picture; ratio = frames with a face / frames looked at).
    None when there is no detector; ratio 0 (and no position) when no face was found."""
    import cv2

    made = make_detector(download)
    if made is None:
        return None
    name, detect = made
    cap = open_capture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(int(round(fps / per_second)), 1)
    seen, found, i = 0, [], 0
    while cap.grab():
        if i % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                seen += 1
                H, W = frame.shape[:2]
                faces = [f for f in detect(frame) if f[3] >= H * MIN_FACE]
                if faces:
                    x, y, w, h, eyes = max(faces, key=lambda f: f[2] * f[3])
                    eyes = y + h / 2 - EYES_ABOVE_CENTER * h if eyes is None else eyes
                    found.append(((x + w / 2) / W, (y + h / 2) / H, h / H, eyes / H))
        i += 1
    cap.release()
    out = {"ratio": round(len(found) / seen, 3) if seen else 0.0, "frames": seen, "detector": name}
    if found:
        x, y, size, eyes = (float(v) for v in np.median(np.asarray(found, float), axis=0))
        out.update(x=round(min(max(x, 0), 1), 4), y=round(min(max(y, 0), 1), 4), size=round(size, 4),
                   eyes=round(min(max(eyes, 0), 1), 4))
    return out
