import json
from pathlib import Path

import cv2
import numpy as np
from pipe1_core.stop_detection import (
    StopFrameCandidateDetectionConfig,
    StopFrameCandidateDetector,
)


def _frame_with_shape(x: int, label: str = "") -> np.ndarray:
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    cv2.rectangle(frame, (x, 58), (x + 76, 124), (190, 190, 190), -1)
    cv2.circle(frame, (x + 38, 91), 18, (40, 40, 40), 2)
    if label:
        cv2.putText(
            frame,
            label,
            (140, 98),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    return frame


def _make_candidate_test_video(path: Path, fps: int = 10) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        float(fps),
        (320, 180),
    )
    assert writer.isOpened()

    for idx in range(20):
        writer.write(_frame_with_shape(20 + idx * 4))
    stable_a = _frame_with_shape(82, "A")
    for _ in range(16):
        writer.write(stable_a)
    for idx in range(12):
        writer.write(_frame_with_shape(82 + idx * 5))
    stable_b = _frame_with_shape(154, "B")
    for _ in range(18):
        writer.write(stable_b)
    for idx in range(14):
        writer.write(_frame_with_shape(154 - idx * 5))

    writer.release()


def test_stop_frame_candidates_are_found_after_motion_bursts(tmp_path: Path) -> None:
    video_path = tmp_path / "candidates.avi"
    _make_candidate_test_video(video_path)

    detector = StopFrameCandidateDetector(
        StopFrameCandidateDetectionConfig(
            fps=5,
            max_candidates_per_segment=3,
            min_stable_duration=0.6,
            min_candidate_gap=1.0,
        )
    )
    segments = detector.analyze(
        str(video_path),
        [{"start_time": 0.0, "end_time": 8.0, "duration": 8.0, "distance_m": 1.2}],
    )

    candidates = segments[0]["candidates"]
    timestamps = [candidate["timestamp"] for candidate in candidates]
    assert len(candidates) >= 2
    assert any(2.0 <= timestamp <= 3.8 for timestamp in timestamps)
    assert any(4.8 <= timestamp <= 6.8 for timestamp in timestamps)
    # The web app stores the result as JSON; internal image signatures must not leak into it.
    json.dumps(segments)


def test_stop_frame_candidates_respect_max_per_segment(tmp_path: Path) -> None:
    video_path = tmp_path / "candidates.avi"
    _make_candidate_test_video(video_path)

    detector = StopFrameCandidateDetector(
        StopFrameCandidateDetectionConfig(
            fps=5,
            max_candidates_per_segment=1,
            min_stable_duration=0.6,
            min_candidate_gap=1.0,
        )
    )
    segments = detector.analyze(
        str(video_path),
        [{"start_time": 0.0, "end_time": 8.0, "duration": 8.0, "distance_m": 1.2}],
    )

    assert len(segments[0]["candidates"]) == 1
