from pathlib import Path

import cv2
import numpy as np

from sewerpipe_inspector.stop_detection import StopDetectionConfig, StopSegmentDetector


def _make_motion_test_video(path: Path, fps: int = 10) -> None:
    size = (320, 180)
    fourcc_fn = getattr(cv2, "VideoWriter_fourcc")
    writer = cv2.VideoWriter(
        str(path),
        fourcc_fn(*"MJPG"),
        float(fps),
        size,
    )
    assert writer.isOpened()

    def moving_frame(idx: int) -> np.ndarray:
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        x = (idx * 4) % (size[0] - 60)
        cv2.rectangle(frame, (x, 40), (x + 60, 120), (200, 200, 200), -1)
        return frame

    static = moving_frame(0)

    for i in range(0, 30):
        writer.write(moving_frame(i))
    for _ in range(30):
        writer.write(static)
    for i in range(30, 33):
        writer.write(moving_frame(i))
    for _ in range(17):
        writer.write(static)
    for i in range(33, 73):
        writer.write(moving_frame(i))

    writer.release()


def _make_slow_motion_test_video(path: Path, fps: int = 10) -> None:
    size = (320, 180)
    fourcc_fn = getattr(cv2, "VideoWriter_fourcc")
    writer = cv2.VideoWriter(
        str(path),
        fourcc_fn(*"MJPG"),
        float(fps),
        size,
    )
    assert writer.isOpened()

    for idx in range(120):
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        x = 20 + (idx % 120)
        cv2.rectangle(frame, (x, 50), (x + 60, 120), (200, 200, 200), -1)
        writer.write(frame)

    writer.release()


def test_stop_segment_detect_and_merge(tmp_path: Path) -> None:
    video_path = tmp_path / "motion.avi"
    _make_motion_test_video(video_path)

    detector = StopSegmentDetector(
        StopDetectionConfig(
            fps=5,
            min_stop_duration=2.0,
            merge_gap_threshold=0.5,
            use_optical_flow=False,
            max_stop_duration=60.0,
        )
    )
    segments = detector.analyze(str(video_path))
    assert len(segments) == 1
    seg = segments[0]
    assert 2.0 <= seg["start_time"] <= 4.5
    assert 5.5 <= seg["end_time"] <= 9.0
    assert seg["duration"] >= 2.0


def test_slow_continuous_motion_is_not_reported_as_stop(tmp_path: Path) -> None:
    video_path = tmp_path / "slow_motion.avi"
    _make_slow_motion_test_video(video_path)

    detector = StopSegmentDetector(
        StopDetectionConfig(
            fps=5,
            min_stop_duration=2.0,
            merge_gap_threshold=0.5,
            use_optical_flow=False,
            max_stop_duration=60.0,
        )
    )

    assert detector.analyze(str(video_path)) == []


def test_stop_segment_optical_flow_and_max_duration_filter(tmp_path: Path) -> None:
    video_path = tmp_path / "motion_of.avi"
    _make_motion_test_video(video_path)

    detector = StopSegmentDetector(
        StopDetectionConfig(
            fps=5,
            min_stop_duration=1.0,
            merge_gap_threshold=0.5,
            use_optical_flow=True,
            max_stop_duration=1.0,
        )
    )
    segments = detector.analyze(str(video_path))
    assert segments == []
