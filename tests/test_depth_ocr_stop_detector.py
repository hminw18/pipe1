import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from pipe1_core.stop_detection.depth_ocr_detector import (
    DepthOcrResult,
    DepthOcrStopDetectionConfig,
    DepthOcrStopSegmentDetector,
    DepthSample,
    detected_digit_slots,
)


def _draw_depth_osd_components(width: int, height: int, offset_x: int, offset_y: int):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for x, y, w, h in (
        (21, 12, 16, 28),
        (40, 12, 16, 28),
        (61, 12, 9, 28),
        (97, 12, 16, 28),
        (116, 20, 16, 20),
        (83, 37, 6, 6),
    ):
        cv2.rectangle(
            image,
            (x + offset_x, y + offset_y),
            (x + offset_x + w - 1, y + offset_y + h - 1),
            (255, 255, 255),
            -1,
        )
    return image


def test_detected_digit_slots_match_baseline_roi() -> None:
    image = _draw_depth_osd_components(149, 53, 0, 0)

    assert detected_digit_slots(image) == (
        (13, 2, 28, 45),
        (34, 2, 29, 45),
        (55, 2, 29, 45),
        (92, 2, 28, 45),
    )


def test_detected_digit_slots_tolerate_loose_roi_padding() -> None:
    image = _draw_depth_osd_components(172, 65, 2, 10)

    assert detected_digit_slots(image) == (
        (15, 12, 28, 45),
        (36, 12, 29, 45),
        (57, 12, 29, 45),
        (94, 12, 28, 45),
    )


def test_depth_series_extracts_stop_segments_and_bridges_short_missing() -> None:
    detector = DepthOcrStopSegmentDetector(
        DepthOcrStopDetectionConfig(
            fps=1.0,
            min_stop_duration=3.0,
            merge_gap_threshold=1.0,
            max_missing_bridge_seconds=2.0,
        )
    )
    values = [
        0.0,
        0.0,
        None,
        0.0,
        0.1,
        0.2,
        1.2,
        1.2,
        None,
        1.2,
        1.2,
        1.4,
    ]
    samples = [
        DepthSample(time=float(idx), raw_value=value, value=value, min_score=None, min_margin=None)
        for idx, value in enumerate(values)
    ]

    cleaned = detector._clean_values(samples)
    segments = detector._extract_segments(samples, cleaned)

    assert cleaned[:4] == [0.0, 0.0, 0.0, 0.0]
    assert segments == [
        {"start_time": 0.0, "end_time": 6.0, "duration": 6.0, "distance_m": 0.0},
        {"start_time": 6.0, "end_time": 12.0, "duration": 6.0, "distance_m": 1.2},
    ]
    # The camera sets off after the last reading at the starting distance.
    assert detector._travel_start(samples, cleaned) == 5.0


def test_travel_start_ignores_a_misread_and_a_counter_that_never_moves() -> None:
    detector = DepthOcrStopSegmentDetector(DepthOcrStopDetectionConfig(fps=1.0))

    def travel_start(values: list[float | None]) -> float | None:
        samples = [
            DepthSample(time=float(i), raw_value=v, value=v, min_score=None, min_margin=None)
            for i, v in enumerate(values)
        ]
        return detector._travel_start(samples, values)

    # Unreadable while being set up, one misread (8.8) at the start, sets off after 6 s.
    assert travel_start([None, None, 0.0, 8.8, 0.0, 0.0, 0.0, 0.8, 1.6, 2.4, 3.2]) == 6.0
    assert travel_start([0.0, 0.0, 0.1, 0.0, 0.2]) is None


# Distance over time for a synthetic inspection: stops (one only 3.5 s long, spanning a
# keyframe), movement at 1 m/s, and a stretch where the counter is unreadable. Sample
# times are whole seconds, where every distance is a multiple of 0.5 m.
_PROFILE = [
    (0.0, 6.0, 0.0, 0.0),
    (6.0, 8.0, 0.0, 2.0),
    (8.0, 11.5, 2.0, 2.0),
    (11.5, 13.5, 2.0, 4.0),
    (13.5, 18.0, 4.0, 4.0),
    (18.0, 21.0, None, None),
    (21.0, 24.0, 4.0, 4.0),
    (24.0, 31.0, 4.0, 11.0),
    (31.0, 40.0, 11.0, 11.0),
]
_ROI = (20, 20, 60, 30)


def _distance_at(seconds: float) -> float | None:
    for start, end, first, last in _PROFILE:
        if start <= seconds < end:
            if first is None or last is None:
                return None
            return round(2 * (first + (last - first) * (seconds - start) / (end - start))) / 2
    return _PROFILE[-1][3]


class _BrightnessCounter:
    """Stands in for the digit matcher: the counter region's brightness encodes the distance."""

    def read_depth_value(self, roi_image, slots):
        level = float(roi_image.mean())
        if level < 10:
            return None
        # Encoding shifts brightness by a few levels; distances are multiples of 0.5 m.
        return DepthOcrResult(round((level - 20) / 10) / 2, (0, 0, 0, 0), 1.0, 1.0)


def _write_profile_video(path: Path, fps: int = 30) -> None:
    x, y, w, h = _ROI
    encoder = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            "160x90",
            "-r",
            str(fps),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-qp",
            "0",
            "-g",
            str(2 * fps),
            "-keyint_min",
            str(2 * fps),
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        stdin=subprocess.PIPE,
    )
    assert encoder.stdin is not None
    for index in range(int(_PROFILE[-1][1] * fps)):
        frame = np.full((90, 160, 3), 128, dtype=np.uint8)
        distance = _distance_at(index / fps)
        frame[y : y + h, x : x + w] = 0 if distance is None else int(round(20 + distance * 20))
        encoder.stdin.write(frame.tobytes())
    encoder.stdin.close()
    assert encoder.wait() == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs the ffmpeg command")
def test_keyframe_first_reading_finds_the_same_stops_with_less_decoding(tmp_path: Path) -> None:
    video = tmp_path / "inspection.mp4"
    _write_profile_video(video)

    def detect(keyframe_first: bool):
        config = DepthOcrStopDetectionConfig(keyframe_first=keyframe_first)
        detector = DepthOcrStopSegmentDetector(config, matcher=_BrightnessCounter())
        segments = detector.analyze(str(video), _ROI, max_workers=2)
        readings = (detector.last_readings, detector.last_travel_start)
        return segments, detector.last_stats, readings

    every_frame, every_frame_stats, every_frame_readings = detect(keyframe_first=False)
    fast, fast_stats, fast_readings = detect(keyframe_first=True)

    # The unreadable 3 s are bridged; the 3.5 s stop spans a keyframe.
    assert [(s["start_time"], s["end_time"], s["distance_m"]) for s in every_frame] == [
        (0.0, 7.0, 0.0),
        (8.0, 13.0, 2.0),
        (13.0, 25.0, 4.0),
        (31.0, 40.0, 11.0),
    ]
    assert fast == every_frame
    # One cleaned reading per second, the gap bridged; the camera sets off after 6 s.
    readings, travel_start = fast_readings
    assert fast_readings == every_frame_readings
    assert readings[:3] == [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    assert len(readings) == 40 and all(value is not None for _, value in readings)
    assert travel_start == 6.0
    assert fast_stats["mode"] == "keyframe_first"
    # Mostly movement, which is decoded; real inspections are mostly stopped.
    assert fast_stats["decoded_frames"] <= every_frame_stats["decoded_frames"] / 2
