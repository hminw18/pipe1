from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2


@dataclass
class VideoMeta:
    duration_seconds: float
    fps: float
    frame_count: int
    width: int
    height: int


class VideoService:
    @staticmethod
    def read_metadata(video_path: Path) -> VideoMeta:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Unable to open video: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = (frame_count / fps) if fps > 0 else 0.0
        cap.release()
        return VideoMeta(
            duration_seconds=duration,
            fps=fps,
            frame_count=frame_count,
            width=width,
            height=height,
        )
