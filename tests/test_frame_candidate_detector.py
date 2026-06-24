from pathlib import Path

import cv2
import numpy as np

from sewerpipe_inspector.stop_detection import (
    StopFrameCandidateDetectionConfig,
    StopFrameCandidateDetector,
)
from sewerpipe_inspector.stop_detection.frame_candidate_detector import MotionSample


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


def test_stop_frame_candidates_do_not_fill_missing_candidates(tmp_path: Path) -> None:
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
        [{"start_time": 1.8, "end_time": 3.8, "duration": 2.0, "distance_m": 1.2}],
    )

    candidates = segments[0]["candidates"]
    assert 0 < len(candidates) < 3
    assert all(candidate["duration"] > 0 for candidate in candidates)


def _candidate_motion_samples(post_kind: str) -> list[MotionSample]:
    samples: list[MotionSample] = []
    signature = np.zeros(256, dtype=bool)
    for idx in range(5):
        samples.append(
            MotionSample(
                time=idx * 0.2,
                motion=10.0,
                sharpness=80.0,
                rotation_motion=10.0,
                translation_motion=0.5,
                signature=signature,
            )
        )
    for idx in range(5, 13):
        samples.append(
            MotionSample(
                time=idx * 0.2,
                motion=0.05,
                sharpness=300.0,
                rotation_motion=0.0,
                translation_motion=0.0,
                signature=signature,
            )
        )
    for idx in range(13, 19):
        is_translation = post_kind == "translation"
        samples.append(
            MotionSample(
                time=idx * 0.2,
                motion=10.0,
                sharpness=80.0,
                rotation_motion=0.5 if is_translation else 10.0,
                translation_motion=10.0 if is_translation else 0.5,
                signature=signature,
            )
        )
    return samples


def test_stop_frame_candidates_keep_rotation_after_stable_plateau() -> None:
    detector = StopFrameCandidateDetector(
        StopFrameCandidateDetectionConfig(
            fps=5,
            min_stable_duration=0.6,
            min_confidence=0.45,
        )
    )

    candidates = detector._select_candidates(
        _candidate_motion_samples("rotation"),
        {"start_time": 0.0, "end_time": 3.6, "duration": 3.6},
    )

    assert len(candidates) == 1
    assert candidates[0]["post_rotation_score"] > candidates[0]["post_translation_score"]
    assert candidates[0]["confidence"] >= 0.45


def test_stop_frame_candidates_keep_departure_pause_when_prioritizing_recall() -> None:
    detector = StopFrameCandidateDetector(
        StopFrameCandidateDetectionConfig(
            fps=5,
            min_stable_duration=0.6,
            min_confidence=0.45,
        )
    )

    candidates = detector._select_candidates(
        _candidate_motion_samples("translation"),
        {"start_time": 0.0, "end_time": 3.6, "duration": 3.6},
    )

    assert len(candidates) == 1
    assert candidates[0]["post_translation_strength"] > 0.5
    assert candidates[0]["departure_penalty"] == 0.0
    assert candidates[0]["confidence"] >= 0.45


def test_stable_run_uses_smoothed_motion_to_ignore_single_sample_spikes() -> None:
    detector = StopFrameCandidateDetector(
        StopFrameCandidateDetectionConfig(
            fps=5,
            min_stable_duration=0.6,
        )
    )
    samples = [
        MotionSample(time=idx * 0.2, motion=motion, sharpness=100.0)
        for idx, motion in enumerate(
            [0.8, 0.8, 0.8, 1.6, 0.8, 0.8, 0.8, 1.6, 0.8, 0.8, 0.8]
        )
    ]
    motions = np.array([sample.motion for sample in samples], dtype=np.float32)
    smoothed = detector._smoothed_motion_values(motions)

    runs = detector._extract_stable_runs(samples, smoothed, stable_threshold=1.0)

    assert runs == [(0, len(samples) - 1)]


def test_stable_run_keeps_one_second_movement_split_after_smoothing() -> None:
    detector = StopFrameCandidateDetector(
        StopFrameCandidateDetectionConfig(
            fps=5,
            min_stable_duration=0.6,
        )
    )
    samples = [
        MotionSample(time=idx * 0.2, motion=motion, sharpness=100.0)
        for idx, motion in enumerate(
            [0.8, 0.8, 0.8, 0.8, 6.0, 8.0, 9.0, 7.0, 6.0, 0.8, 0.8, 0.8, 0.8]
        )
    ]
    motions = np.array([sample.motion for sample in samples], dtype=np.float32)
    smoothed = detector._smoothed_motion_values(motions)

    runs = detector._extract_stable_runs(samples, smoothed, stable_threshold=1.0)

    assert runs == [(0, 3), (9, 12)]


def test_motion_window_score_uses_raw_peak_and_energy() -> None:
    detector = StopFrameCandidateDetector()

    weak_spike = detector._motion_window_metrics([0.8, 1.6, 0.8], 1.2)
    one_second_move = detector._motion_window_metrics([6.0, 8.0, 9.0, 7.0, 6.0], 1.2)
    short_strong_move = detector._motion_window_metrics([20.0, 24.0], 1.2)

    assert weak_spike["score"] < 2.0
    assert one_second_move["score"] > 9.0
    assert short_strong_move["score"] > 20.0
