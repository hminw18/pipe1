import cv2
import numpy as np

from sewerpipe_inspector.stop_detection.depth_ocr_detector import (
    DepthOcrStopDetectionConfig,
    DepthOcrStopSegmentDetector,
    DepthSample,
    detected_digit_slots,
    scaled_digit_slots,
)


def test_scaled_digit_slots_match_baseline_roi() -> None:
    assert scaled_digit_slots(149, 53) == (
        (13, 2, 28, 45),
        (34, 2, 29, 45),
        (55, 2, 29, 45),
        (92, 2, 28, 45),
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


def test_depth_series_treats_half_meter_movement_as_one_segment() -> None:
    detector = DepthOcrStopSegmentDetector(
        DepthOcrStopDetectionConfig(
            fps=1.0,
            min_stop_duration=3.0,
            stop_distance_tolerance_m=0.5,
            merge_gap_threshold=1.0,
        )
    )
    values = [10.0, 10.1, 10.4, 10.5, 10.3, 10.0, 11.0, 11.1, 11.2]
    samples = [
        DepthSample(time=float(idx), raw_value=value, value=value, min_score=None, min_margin=None)
        for idx, value in enumerate(values)
    ]

    segments = detector._extract_segments(samples, values)

    assert segments == [
        {"start_time": 0.0, "end_time": 6.0, "duration": 6.0, "distance_m": 10.2},
        {"start_time": 6.0, "end_time": 9.0, "duration": 3.0, "distance_m": 11.1},
    ]


def test_depth_series_does_not_chain_half_meter_steps_indefinitely() -> None:
    detector = DepthOcrStopSegmentDetector(
        DepthOcrStopDetectionConfig(
            fps=1.0,
            min_stop_duration=3.0,
            stop_distance_tolerance_m=0.5,
        )
    )
    values = [20.0, 20.2, 20.5, 20.7, 20.9, 21.1]
    samples = [
        DepthSample(time=float(idx), raw_value=value, value=value, min_score=None, min_margin=None)
        for idx, value in enumerate(values)
    ]

    segments = detector._extract_segments(samples, values)

    assert segments == [
        {"start_time": 0.0, "end_time": 3.0, "duration": 3.0, "distance_m": 20.2},
        {"start_time": 3.0, "end_time": 6.0, "duration": 3.0, "distance_m": 20.9},
    ]
