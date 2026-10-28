from __future__ import annotations

import sys
from pathlib import Path

import cv2


def open_analysis_video_capture(path: str | Path) -> cv2.VideoCapture:
    path_str = str(path)
    if sys.platform.startswith("win") and hasattr(cv2, "CAP_FFMPEG"):
        cap = cv2.VideoCapture(path_str, cv2.CAP_FFMPEG)
        if cap.isOpened():
            return cap
        cap.release()
    return cv2.VideoCapture(path_str)
