from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Optional

import cv2

from sewerpipe_inspector.stop_detection.depth_ocr_detector import (
    CommonDepthDigitMatcher,
)

try:
    import pytesseract
except ImportError:
    pytesseract = None


NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:[\.,]\d+)?")
METER_VALUE_PATTERN = re.compile(r"([-+]?\d+(?:[\.,]\d+)?)\s*[mM]")
METER_DIGITS_PATTERN = re.compile(r"([-+]?\d{2,6})\s*[mM]")
DEFAULT_MIN_DIGIT_SCORE = 0.5
DEFAULT_MIN_DIGIT_MARGIN = 0.0


def _normalize_ocr_text(text: str) -> str:
    table = str.maketrans(
        {
            "O": "0",
            "o": "0",
            "I": "1",
            "l": "1",
            "|": "1",
            "S": "5",
            "s": "5",
            "B": "8",
        }
    )
    return text.translate(table)


def _extract_best_number(text: str) -> Optional[float]:
    normalized = _normalize_ocr_text(text)
    candidates: list[tuple[float, int]] = []

    for match in METER_VALUE_PATTERN.finditer(normalized):
        token = match.group(1)
        value = float(token.replace(",", "."))
        digits_only = re.sub(r"\D", "", token)
        score = 120 + len(digits_only)
        if "." in token or "," in token:
            score += 20
        candidates.append((value, score))

    for match in METER_DIGITS_PATTERN.finditer(normalized):
        token = match.group(1)
        if "." in token or "," in token:
            continue
        raw_value = float(token)
        digits_only = re.sub(r"\D", "", token)
        candidates.append((raw_value / 10.0, 105 + len(digits_only)))
        candidates.append((raw_value, 70 + len(digits_only)))

    for match in NUMBER_PATTERN.finditer(normalized):
        token = match.group(0)
        value = float(token.replace(",", "."))
        digits_only = re.sub(r"\D", "", token)
        score = 20 + len(digits_only)
        if "." in token or "," in token:
            score += 20
        candidates.append((value, score))

    if not candidates:
        return None

    best_value, _best_score = max(candidates, key=lambda item: item[1])
    return best_value


class OCRService:
    def __init__(
        self,
        matcher: CommonDepthDigitMatcher | None = None,
        min_digit_score: float = DEFAULT_MIN_DIGIT_SCORE,
        min_digit_margin: float = DEFAULT_MIN_DIGIT_MARGIN,
        tesseract_reader: Callable[[object, str], str] | None = None,
    ) -> None:
        self.matcher = matcher or CommonDepthDigitMatcher()
        self.min_digit_score = min_digit_score
        self.min_digit_margin = min_digit_margin
        self.tesseract_reader = tesseract_reader
        self.logger = logging.getLogger(self.__class__.__name__)

    def is_ready(self) -> bool:
        return True

    def read_depth_value(
        self, frame, roi: tuple[int, int, int, int]
    ) -> Optional[float]:
        crop = self._crop_depth_roi(frame, roi)
        if crop is None:
            return None

        value = self._read_depth_value_opencv(crop)
        if value is not None:
            return value
        return self._read_depth_value_tesseract(crop)

    def _crop_depth_roi(self, frame, roi: tuple[int, int, int, int]):
        x, y, w, h = (int(value) for value in roi)
        if frame is None or x < 0 or y < 0 or w <= 0 or h <= 0:
            return None

        crop = frame[y : y + h, x : x + w]
        if crop.size == 0:
            return None

        if len(crop.shape) == 2:
            return cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        if crop.shape[2] == 4:
            return cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR)
        return crop

    def _read_depth_value_opencv(self, crop) -> Optional[float]:
        try:
            result = self.matcher.read_depth_value(crop, None)
        except Exception:
            self.logger.exception("OpenCV depth OCR failed")
            return None

        if result is None:
            return None
        if result.min_score < self.min_digit_score:
            return None
        if result.min_margin < self.min_digit_margin:
            return None
        return float(result.value)

    def _read_depth_value_tesseract(self, crop) -> Optional[float]:
        if self.tesseract_reader is None and not self._is_tesseract_ready():
            return None

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        blurred = cv2.GaussianBlur(upscaled, (3, 3), 0)

        _, bin_inv = cv2.threshold(
            blurred,
            0,
            255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        )
        _, bin_norm = cv2.threshold(
            blurred,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )
        adaptive = cv2.adaptiveThreshold(
            blurred,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            4,
        )

        variants = [upscaled, bin_inv, bin_norm, adaptive]
        configs = [
            "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,-mM",
            "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789.,-mM",
        ]

        best_value: Optional[float] = None
        best_rank = -1

        for image in variants:
            for config in configs:
                try:
                    text = self._run_tesseract(image, config)
                except Exception:
                    continue
                value = _extract_best_number(text)
                if value is None:
                    continue
                token_text = _normalize_ocr_text(text)
                has_meter = "m" in token_text.lower()
                has_decimal = "." in token_text or "," in token_text
                rank = (
                    (30 if has_meter else 0)
                    + (10 if has_decimal else 0)
                    + len(re.sub(r"\D", "", token_text))
                )
                if rank > best_rank:
                    best_rank = rank
                    best_value = value
        return best_value

    def _is_tesseract_ready(self) -> bool:
        if pytesseract is None:
            return False
        try:
            _ = pytesseract.get_tesseract_version()
        except Exception:
            return False
        return True

    def _run_tesseract(self, image, config: str) -> str:
        if self.tesseract_reader is not None:
            return self.tesseract_reader(image, config)
        if pytesseract is None:
            return ""
        return pytesseract.image_to_string(image, config=config)
