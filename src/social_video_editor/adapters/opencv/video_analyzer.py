"""VideoAnalyzer with OpenCV (+ PySceneDetect for scene changes)."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from ...log import log


class OpenCvVideoAnalyzer:
    def samples(self, video: str, per_second: float) -> list[dict]:
        """Brightness, sharpness, motion and colorfulness several times per second (on the proxy)."""
        import cv2

        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        step = max(int(round(fps / per_second)), 1)
        out, prev, i = [], None, 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if i % step == 0:
                _, f = cap.retrieve()
                small = cv2.resize(f, (180, int(180 * f.shape[0] / f.shape[1])))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
                motion = float(np.mean(np.abs(gray - prev))) if prev is not None else 0.0
                prev = gray
                b, g, r = [small[..., k].astype(np.float32) for k in range(3)]
                rg, yb = np.abs(r - g), np.abs(0.5 * (r + g) - b)
                color = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
                out.append({
                    "t": round(i / fps, 3),
                    "brightness": round(float(gray.mean() / 255), 4),
                    "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_32F).var()), 2),
                    "motion": round(motion, 3),
                    "color": round(color, 2),
                })
            i += 1
        cap.release()
        return out

    def scenes(self, video: str, threshold: float) -> list[list[float]]:
        try:
            from scenedetect import ContentDetector, detect
            found = detect(video, ContentDetector(threshold=threshold), show_progress=False)
            return [[round(a.get_seconds(), 3), round(b.get_seconds(), 3)] for a, b in found]
        except Exception as e:  # noqa: BLE001
            log(f"Warning: scene detection failed ({e}); using a single scene.")
            return []

    def read_frames(self, video: str, times: Iterable[float]) -> list:
        """BGR frames at the given seconds (frames that cannot be read are skipped)."""
        import cv2

        cap = cv2.VideoCapture(video)
        frames = []
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, f = cap.read()
            if ok:
                frames.append(f)
        cap.release()
        return frames

    def frame_reader(self, video: str):
        """A function t → RGB frame (or None), for browsing a video (contact sheets). Call .close() at the end."""
        import cv2

        cap = cv2.VideoCapture(video)

        def read(t: float):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, f = cap.read()
            return cv2.cvtColor(f, cv2.COLOR_BGR2RGB) if ok else None

        read.close = cap.release
        return read
