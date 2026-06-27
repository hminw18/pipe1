from __future__ import annotations

import base64
import logging
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .depth_template_data import (
    COMMON_DEPTH_TEMPLATE_DTYPE,
    COMMON_DEPTH_TEMPLATE_SHAPE,
    COMMON_DEPTH_TEMPLATE_ZLIB_BASE64,
)
from .video_capture import open_analysis_video_capture


BASE_ROI_SIZE = (149, 53)
BASE_DIGIT_SLOTS = (
    (13, 2, 28, 45),
    (34, 2, 29, 45),
    (55, 2, 29, 45),
    (92, 2, 28, 45),
)
BASE_DIGIT_COMPONENT_HEIGHT = 28
BASE_DIGIT_COMPONENT_LEFT_PADS = (8, 6, 6, 5)
BASE_SLOT_TOP_PAD = 10
BASE_SLOT_HEIGHT = 45
CALIBRATION_SAMPLE_SECONDS = (
    0.0,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    30.0,
    36.0,
    45.0,
    60.0,
    90.0,
    120.0,
    180.0,
    240.0,
    300.0,
    360.0,
    420.0,
)


@dataclass
class DepthOcrStopDetectionConfig:
    fps: float = 1.0
    min_stop_duration: float = 3.0
    stop_distance_tolerance_m: float = 0.5
    merge_gap_threshold: float = 1.0
    max_missing_bridge_seconds: float = 3.0
    min_digit_score: float = 0.5
    min_digit_margin: float = 0.0


@dataclass(frozen=True)
class DepthOcrResult:
    value: float
    digits: tuple[int, int, int, int]
    min_score: float
    min_margin: float


@dataclass(frozen=True)
class DepthSample:
    time: float
    raw_value: float | None
    value: float | None
    min_score: float | None
    min_margin: float | None


@dataclass(frozen=True)
class _TextComponent:
    x: int
    y: int
    w: int
    h: int
    area: int
    cx: float
    cy: float

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


def scaled_digit_slots(roi_w: int, roi_h: int) -> tuple[tuple[int, int, int, int], ...]:
    base_w, base_h = BASE_ROI_SIZE
    scale_x = roi_w / base_w
    scale_y = roi_h / base_h
    slots: list[tuple[int, int, int, int]] = []
    for x, y, w, h in BASE_DIGIT_SLOTS:
        sx = max(0, int(round(x * scale_x)))
        sy = max(0, int(round(y * scale_y)))
        sw = max(1, int(round(w * scale_x)))
        sh = max(1, int(round(h * scale_y)))
        if sx + sw > roi_w:
            sw = max(1, roi_w - sx)
        if sy + sh > roi_h:
            sh = max(1, roi_h - sy)
        slots.append((sx, sy, sw, sh))
    return tuple(slots)


def detected_digit_slots(roi_image: np.ndarray) -> tuple[tuple[int, int, int, int], ...] | None:
    """Find OSD digit slots inside a loosely selected depth ROI."""
    if roi_image.size == 0:
        return None

    roi_h, roi_w = roi_image.shape[:2]
    components = _text_components(_bright_text_mask(roi_image))
    digits = _select_depth_digit_components(components)
    if digits is None:
        return None

    median_digit_h = float(np.median([component.h for component in digits]))
    if median_digit_h <= 0:
        return None

    scale = max(0.5, min(3.0, median_digit_h / BASE_DIGIT_COMPONENT_HEIGHT))
    slot_y = int(round(min(component.y for component in digits) - (BASE_SLOT_TOP_PAD * scale)))
    slot_h = max(1, int(round(BASE_SLOT_HEIGHT * scale)))
    if slot_y < 0:
        slot_y = 0
    if slot_y + slot_h > roi_h:
        slot_y = max(0, roi_h - slot_h)
        slot_h = roi_h - slot_y

    slots: list[tuple[int, int, int, int]] = []
    for idx, component in enumerate(digits):
        base_w = BASE_DIGIT_SLOTS[idx][2]
        left_pad = BASE_DIGIT_COMPONENT_LEFT_PADS[idx]
        slot_w = max(1, int(round(base_w * scale)))
        slot_x = int(round(component.x - (left_pad * scale)))
        if slot_x < 0:
            slot_x = 0
        if slot_x + slot_w > roi_w:
            slot_x = max(0, roi_w - slot_w)
        slots.append((slot_x, slot_y, slot_w, slot_h))

    return tuple(slots)


def _bright_text_mask(roi_image: np.ndarray) -> np.ndarray:
    if len(roi_image.shape) == 2:
        gray = roi_image
    else:
        gray = cv2.cvtColor(roi_image, cv2.COLOR_BGR2GRAY)
    p90 = float(np.percentile(gray, 90))
    p50 = float(np.percentile(gray, 50))
    threshold = max(150.0, min(235.0, p50 + (0.55 * (p90 - p50))))
    return (gray >= threshold).astype(np.uint8)


def _text_components(mask: np.ndarray) -> list[_TextComponent]:
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    components: list[_TextComponent] = []
    for idx in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[idx])
        if area < 8:
            continue
        components.append(
            _TextComponent(
                x=x,
                y=y,
                w=w,
                h=h,
                area=area,
                cx=float(centroids[idx][0]),
                cy=float(centroids[idx][1]),
            )
        )
    return components


def _select_depth_digit_components(
    components: list[_TextComponent],
) -> tuple[_TextComponent, _TextComponent, _TextComponent, _TextComponent] | None:
    tall_components = sorted(
        [
            component
            for component in components
            if component.h >= 12 and component.w >= 3 and component.area >= 30
        ],
        key=lambda component: (component.x, component.y),
    )
    if len(tall_components) < 4:
        return None

    small_components = [
        component
        for component in components
        if 2 <= component.w <= 12 and 2 <= component.h <= 12 and component.area <= 100
    ]

    best: tuple[float, tuple[_TextComponent, _TextComponent, _TextComponent, _TextComponent]] | None = None
    for start in range(len(tall_components) - 3):
        digits = tuple(tall_components[start : start + 4])
        heights = [component.h for component in digits]
        median_height = float(np.median(heights))
        if median_height <= 0:
            continue
        if max(abs(height - median_height) for height in heights) > max(12.0, median_height * 0.7):
            continue
        if max(component.y for component in digits) - min(component.y for component in digits) > median_height * 0.5:
            continue

        third = digits[2]
        fourth = digits[3]
        has_decimal = any(
            third.right - 3 <= component.cx <= fourth.x + 3
            and component.cy >= min(digit.y for digit in digits) + (median_height * 0.55)
            for component in small_components
        )
        has_meter_component = (
            start + 4 < len(tall_components)
            and tall_components[start + 4].x > fourth.x
            and tall_components[start + 4].x - fourth.right <= median_height * 1.2
        )
        score = median_height * 2.0
        score += 25.0 if has_decimal else -10.0
        score += 8.0 if has_meter_component else 0.0
        score -= start * 5.0

        if best is None or score > best[0]:
            best = (score, digits)

    return None if best is None else best[1]


def _normalized_vector(image: np.ndarray) -> np.ndarray:
    vector = image.reshape(-1).astype(np.float32)
    if vector.max(initial=0) > 1.0:
        vector /= 255.0
    vector -= float(vector.mean())
    norm = float(np.linalg.norm(vector))
    if norm > 1e-6:
        vector /= norm
    return vector


def _load_template_matrix() -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(COMMON_DEPTH_TEMPLATE_ZLIB_BASE64))
    matrix = np.frombuffer(raw, dtype=np.dtype(COMMON_DEPTH_TEMPLATE_DTYPE))
    matrix = matrix.reshape(COMMON_DEPTH_TEMPLATE_SHAPE).astype(np.float32)
    return matrix


class CommonDepthDigitMatcher:
    def __init__(self) -> None:
        self.template_matrix = _load_template_matrix()

    def read_depth_value(
        self,
        roi_image: np.ndarray,
        slots: tuple[tuple[int, int, int, int], ...] | None = None,
    ) -> DepthOcrResult | None:
        if roi_image.size == 0:
            return None

        results: list[DepthOcrResult] = []
        for candidate_slots in self._candidate_slots(roi_image, slots):
            result = self._read_depth_value_for_slots(roi_image, candidate_slots)
            if result is not None:
                results.append(result)

        if not results:
            return None
        return max(results, key=lambda result: (result.min_score, result.min_margin))

    def _candidate_slots(
        self,
        roi_image: np.ndarray,
        slots: tuple[tuple[int, int, int, int], ...] | None,
    ) -> tuple[tuple[tuple[int, int, int, int], ...], ...]:
        if slots is not None:
            return (slots,)

        roi_h, roi_w = roi_image.shape[:2]
        candidates: list[tuple[tuple[int, int, int, int], ...]] = [
            scaled_digit_slots(roi_w, roi_h)
        ]
        detected = detected_digit_slots(roi_image)
        if detected is not None and detected not in candidates:
            candidates.append(detected)
        return tuple(candidates)

    def _read_depth_value_for_slots(
        self,
        roi_image: np.ndarray,
        slots: tuple[tuple[int, int, int, int], ...],
    ) -> DepthOcrResult | None:
        if len(slots) != 4:
            return None

        digits: list[int] = []
        best_scores: list[float] = []
        margins: list[float] = []

        for slot in slots:
            digit_image = self._extract_digit_image(roi_image, slot)
            vector = _normalized_vector(digit_image)
            scores = self.template_matrix @ vector
            if scores.size < 2:
                return None
            order = np.argsort(scores)
            best_digit = int(order[-1])
            best_score = float(scores[best_digit])
            second_score = float(scores[int(order[-2])])
            digits.append(best_digit)
            best_scores.append(best_score)
            margins.append(best_score - second_score)

        value = round(
            (digits[0] * 100) + (digits[1] * 10) + digits[2] + (digits[3] / 10.0),
            1,
        )
        return DepthOcrResult(
            value=value,
            digits=(digits[0], digits[1], digits[2], digits[3]),
            min_score=min(best_scores),
            min_margin=min(margins),
        )

    def _extract_digit_image(
        self, roi_image: np.ndarray, slot: tuple[int, int, int, int]
    ) -> np.ndarray:
        x, y, w, h = slot
        chip = roi_image[y : y + h, x : x + w]
        gray = cv2.cvtColor(chip, cv2.COLOR_BGR2GRAY)

        p90 = float(np.percentile(gray, 90))
        p50 = float(np.percentile(gray, 50))
        threshold = max(150.0, min(235.0, p50 + (0.55 * (p90 - p50))))
        mask = (gray >= threshold).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        bbox = self._digit_bbox(mask, w, h)
        bx0, by0, bx1, by1 = bbox
        digit = gray[by0:by1, bx0:bx1]
        return self._center_digit(digit, gray)

    @staticmethod
    def _digit_bbox(mask: np.ndarray, slot_w: int, slot_h: int) -> tuple[int, int, int, int]:
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        del labels

        target_x = slot_w / 2.0
        candidates: list[tuple[float, int, int, int, int, int, float]] = []
        for idx in range(1, count):
            x, y, w, h, area = stats[idx]
            if area < 8 or h < 10 or w < 2:
                continue
            center_x = float(centroids[idx][0])
            score = (area * 2.0) + (h * 3.0) - (abs(center_x - target_x) * 5.0)
            candidates.append((score, x, y, w, h, area, center_x))

        if candidates:
            candidates.sort(reverse=True)
            _score, x, y, w, h, _area, center_x = candidates[0]
            boxes = [(x, y, x + w, y + h)]
            for _score, x2, y2, w2, h2, _area2, center_x2 in candidates[1:]:
                if abs(center_x2 - center_x) <= max(6, w * 0.75):
                    boxes.append((x2, y2, x2 + w2, y2 + h2))
            bx0 = min(box[0] for box in boxes)
            by0 = min(box[1] for box in boxes)
            bx1 = max(box[2] for box in boxes)
            by1 = max(box[3] for box in boxes)
        else:
            ys, xs = np.where(mask > 0)
            if len(xs) == 0:
                bx0, by0, bx1, by1 = 0, 0, slot_w, slot_h
            else:
                bx0, by0, bx1, by1 = (
                    int(xs.min()),
                    int(ys.min()),
                    int(xs.max()) + 1,
                    int(ys.max()) + 1,
                )

        pad = 5
        return (
            max(0, bx0 - pad),
            max(0, by0 - pad),
            min(slot_w, bx1 + pad),
            min(slot_h, by1 + pad),
        )

    @staticmethod
    def _center_digit(digit: np.ndarray, slot_gray: np.ndarray) -> np.ndarray:
        out_w, out_h = 32, 48
        canvas = np.full((out_h, out_w), int(np.median(slot_gray)), dtype=np.uint8)
        digit_h, digit_w = digit.shape[:2]
        scale = min((out_w - 4) / max(1, digit_w), (out_h - 4) / max(1, digit_h))
        target_w = max(1, int(round(digit_w * scale)))
        target_h = max(1, int(round(digit_h * scale)))
        resized = cv2.resize(digit, (target_w, target_h), interpolation=cv2.INTER_CUBIC)
        offset_x = (out_w - target_w) // 2
        offset_y = (out_h - target_h) // 2
        canvas[offset_y : offset_y + target_h, offset_x : offset_x + target_w] = resized
        return canvas


class DepthOcrStopSegmentDetector:
    def __init__(
        self,
        config: DepthOcrStopDetectionConfig | None = None,
        matcher: CommonDepthDigitMatcher | None = None,
    ) -> None:
        self.config = config or DepthOcrStopDetectionConfig()
        self.matcher = matcher or CommonDepthDigitMatcher()
        self.logger = logging.getLogger(self.__class__.__name__)

    def analyze(
        self,
        video_path: str,
        depth_roi: tuple[int, int, int, int],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[dict[str, float]]:
        started = time.perf_counter()
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(str(path))

        cap = open_analysis_video_capture(path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        samples = self._read_depth_samples(cap, depth_roi, progress_callback)
        cap.release()

        if progress_callback is not None:
            progress_callback(1, 1, "거리 OCR 결과 정리")
        cleaned_values = self._clean_values(samples)
        segments = self._extract_segments(samples, cleaned_values)
        segments = self._merge_same_distance_segments(segments)

        elapsed = time.perf_counter() - started
        valid_count = sum(1 for sample in samples if sample.value is not None)
        self.logger.info(
            "Depth OCR stop detection done: samples=%d valid=%d segments=%d time=%.3fs",
            len(samples),
            valid_count,
            len(segments),
            elapsed,
        )
        return segments

    def _read_depth_samples(
        self,
        cap: cv2.VideoCapture,
        depth_roi: tuple[int, int, int, int],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[DepthSample]:
        native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        sample_fps = max(0.1, float(self.config.fps))
        sample_period = 1.0 / sample_fps
        next_sample_time = 0.0
        frame_index = 0
        x, y, w, h = depth_roi
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        progress_interval = max(1, int(round(max(native_fps, 1.0) * 2.0)))
        if progress_callback is not None:
            progress_callback(0, max(1, frame_count), "거리 OCR 보정")
        calibrated_slots = self._calibrate_digit_slots(cap, depth_roi)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        samples: list[DepthSample] = []

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            timestamp = frame_index / native_fps if native_fps > 0 else next_sample_time
            if timestamp + 1e-9 >= next_sample_time:
                roi_image = frame[y : y + h, x : x + w]
                result = self.matcher.read_depth_value(roi_image, calibrated_slots)
                raw_value: float | None = None
                value: float | None = None
                min_score: float | None = None
                min_margin: float | None = None
                if result is not None:
                    raw_value = result.value
                    min_score = result.min_score
                    min_margin = result.min_margin
                    if (
                        result.min_score >= self.config.min_digit_score
                        and result.min_margin >= self.config.min_digit_margin
                    ):
                        value = result.value
                samples.append(
                    DepthSample(
                        time=timestamp,
                        raw_value=raw_value,
                        value=value,
                        min_score=min_score,
                        min_margin=min_margin,
                    )
                )
                next_sample_time += sample_period
                if (
                    progress_callback is not None
                    and frame_count > 0
                    and frame_index % progress_interval == 0
                ):
                    progress_callback(
                        frame_index,
                        frame_count,
                        f"거리 OCR 샘플 {len(samples)}개",
                    )
            frame_index += 1

        if progress_callback is not None:
            progress_callback(max(frame_index, frame_count), max(1, frame_count), "거리 OCR 완료")
        return samples

    def _calibrate_digit_slots(
        self,
        cap: cv2.VideoCapture,
        depth_roi: tuple[int, int, int, int],
    ) -> tuple[tuple[int, int, int, int], ...] | None:
        x, y, w, h = depth_roi
        native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / native_fps if native_fps > 0 and frame_count > 0 else 0.0
        best: tuple[float, float, tuple[tuple[int, int, int, int], ...]] | None = None

        for sample_time in CALIBRATION_SAMPLE_SECONDS:
            if duration > 0 and sample_time > duration:
                continue
            cap.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1000)
            ok, frame = cap.read()
            if not ok:
                continue

            roi_image = frame[y : y + h, x : x + w]
            slots = detected_digit_slots(roi_image)
            if slots is None:
                continue

            result = self.matcher.read_depth_value(roi_image, slots)
            if result is None:
                continue

            candidate = (result.min_score, result.min_margin, slots)
            if best is None or candidate[:2] > best[:2]:
                best = candidate

        return None if best is None else best[2]

    def _clean_values(self, samples: list[DepthSample]) -> list[float | None]:
        values = [sample.value for sample in samples]
        if not values:
            return values

        max_gap = max(0, int(round(self.config.max_missing_bridge_seconds * self.config.fps)))
        if max_gap == 0:
            return values

        cleaned = list(values)
        for gap in range(1, max_gap + 1):
            idx = 1
            while idx < len(cleaned) - gap:
                left = cleaned[idx - 1]
                right = cleaned[idx + gap]
                if (
                    left is not None
                    and right is not None
                    and self._within_stop_distance_tolerance(left, right)
                ):
                    should_fill = any(cleaned[idx + offset] != left for offset in range(gap))
                    if should_fill:
                        for offset in range(gap):
                            cleaned[idx + offset] = left
                        idx += gap
                        continue
                idx += 1

        return cleaned

    def _extract_segments(
        self,
        samples: list[DepthSample],
        values: list[float | None],
    ) -> list[dict[str, float]]:
        if not samples or not values:
            return []

        sample_period = 1.0 / max(0.1, float(self.config.fps))
        segments: list[dict[str, float]] = []
        run_anchor: float | None = None
        run_values: list[float] = []
        run_start: float | None = None
        previous_time = samples[0].time

        def close_run(end_time: float) -> None:
            nonlocal run_anchor, run_values, run_start
            if run_anchor is None or run_start is None or not run_values:
                return
            duration = end_time - run_start
            if duration >= self.config.min_stop_duration:
                representative_distance = float(round(float(np.median(run_values)), 1))
                segments.append(
                    {
                        "start_time": float(run_start),
                        "end_time": float(end_time),
                        "duration": float(duration),
                        "distance_m": representative_distance,
                    }
                )

        for sample, value in zip(samples, values):
            if value is None:
                close_run(sample.time)
                run_anchor = None
                run_values = []
                run_start = None
                previous_time = sample.time
                continue

            if run_anchor is None:
                run_anchor = value
                run_values = [value]
                run_start = sample.time
            elif self._within_stop_distance_tolerance(run_anchor, value):
                run_values.append(value)
            else:
                close_run(sample.time)
                run_anchor = value
                run_values = [value]
                run_start = sample.time
            previous_time = sample.time

        close_run(previous_time + sample_period)
        return segments

    def _merge_same_distance_segments(
        self, segments: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        if not segments:
            return []

        merged = [dict(segments[0])]
        for segment in segments[1:]:
            last = merged[-1]
            gap = segment["start_time"] - last["end_time"]
            same_distance = self._within_stop_distance_tolerance(
                segment.get("distance_m"),
                last.get("distance_m"),
            )
            if same_distance and gap <= self.config.merge_gap_threshold:
                last["end_time"] = max(last["end_time"], segment["end_time"])
                last["duration"] = last["end_time"] - last["start_time"]
            else:
                merged.append(dict(segment))
        return merged

    def _within_stop_distance_tolerance(
        self, left: float | None, right: float | None
    ) -> bool:
        if left is None or right is None:
            return False
        tolerance = max(0.0, float(self.config.stop_distance_tolerance_m))
        return abs(float(left) - float(right)) <= tolerance
