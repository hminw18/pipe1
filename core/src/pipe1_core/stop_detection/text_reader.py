"""Depth counter reading with a pretrained text recognizer (PP-OCRv4 on ONNX Runtime).

Reads any counter font and format ("013.50m", "12.3", "128.0 M") without per-recorder digit
templates. The user draws the depth region, so only the recognizer runs, never text detection.
Needs the optional onnxruntime package (pipe1-core[ocr]); without it, default_depth_reader()
falls back to the template matcher.
"""

from __future__ import annotations

import math
import re
import threading

import cv2
import numpy as np

from ..resources import find_resource
from .depth_ocr_detector import CommonDepthDigitMatcher, DepthOcrResult

MODEL_RESOURCE = "models/ocr/ch_PP-OCRv4_rec_infer.onnx"
# The model reads thousands of characters; the counter only ever shows these. No minus sign:
# noise left of the digits was read as one, and captures store distances without a sign.
COUNTER_CHARACTERS = "0123456789.mM"
_INPUT_HEIGHT = 48
_MAX_INPUT_WIDTH = 48 * 25
# Digits touching the edge of the depth region are often misread (7 as 9, 3 as 8); a thin black
# border cut misreads on a real inspection video from 16 to 1 in 939 readings.
_BORDER = 0.05
_DISTANCE = re.compile(r"\d+(?:\.\d+)?")

try:
    import onnxruntime
except ImportError:  # the desktop build does not ship it yet
    onnxruntime = None


def parse_distance(text: str) -> float | None:
    """Metres from counter text such as "013.50m"; None unless the text is one number."""
    number = re.sub(r"\.+", ".", text.strip().rstrip("mM").strip())
    if not _DISTANCE.fullmatch(number):
        return None
    return float(number)


class TextCounterReader:
    """Same interface as CommonDepthDigitMatcher; the digit slots are not needed."""

    uses_slots = False

    def __init__(self, model_path: str | None = None) -> None:
        if onnxruntime is None:
            raise RuntimeError("onnxruntime is not installed")
        path = model_path or find_resource(MODEL_RESOURCE)
        if path is None:
            raise FileNotFoundError(MODEL_RESOURCE)
        self._session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        # Class 0 is the CTC blank, then the model's character list, then a space.
        metadata = self._session.get_modelmeta().custom_metadata_map
        self._characters = ["", *metadata["character"].splitlines(), " "]
        self._allowed = np.array(
            [0, *(i for i, c in enumerate(self._characters) if c and c in COUNTER_CHARACTERS)]
        )

    def read_depth_value(
        self,
        roi_image: np.ndarray,
        slots: tuple[tuple[int, int, int, int], ...] | None = None,
    ) -> DepthOcrResult | None:
        text, score, margin = self.read_text(roi_image)
        value = parse_distance(text)
        if value is None:
            return None
        return DepthOcrResult(value=value, digits=(), min_score=score, min_margin=margin)

    def read_text(self, roi_image: np.ndarray) -> tuple[str, float, float]:
        """(text, lowest character confidence, lowest margin over the runner-up character)."""
        if roi_image.size == 0:
            return "", 0.0, 0.0
        image = roi_image if roi_image.ndim == 3 else cv2.cvtColor(roi_image, cv2.COLOR_GRAY2BGR)
        border = max(1, round(_BORDER * image.shape[0]))
        image = cv2.copyMakeBorder(image, border, border, border, border, cv2.BORDER_CONSTANT)
        height, width = image.shape[:2]
        input_width = min(_MAX_INPUT_WIDTH, max(16, math.ceil(_INPUT_HEIGHT * width / height)))
        tensor = cv2.resize(image, (input_width, _INPUT_HEIGHT)).astype(np.float32)
        tensor = (tensor / 127.5 - 1.0).transpose(2, 0, 1)[None]
        probabilities = self._session.run(None, {self._input_name: tensor})[0][0]

        allowed = probabilities[:, self._allowed]
        order = np.argsort(allowed, axis=1)
        best = order[:, -1]
        rows = np.arange(len(allowed))
        confidence = allowed[rows, best]
        margin = confidence - allowed[rows, order[:, -2]]

        characters: list[str] = []
        scores: list[float] = []
        margins: list[float] = []
        previous = -1
        for step, column in enumerate(best):
            label = int(self._allowed[column])
            if label != previous and label != 0:
                characters.append(self._characters[label])
                scores.append(float(confidence[step]))
                margins.append(float(margin[step]))
            previous = label
        if not characters:
            return "", 0.0, 0.0
        return "".join(characters), min(scores), min(margins)


_default_lock = threading.Lock()
_default_reader: TextCounterReader | None = None


def default_depth_reader() -> TextCounterReader | CommonDepthDigitMatcher:
    """The text recognizer when onnxruntime and the model are present, else digit templates.

    The recognizer is loaded once per process and shared: ONNX Runtime sessions are thread-safe.
    """
    global _default_reader
    if onnxruntime is None or find_resource(MODEL_RESOURCE) is None:
        return CommonDepthDigitMatcher()
    with _default_lock:
        if _default_reader is None:
            _default_reader = TextCounterReader()
        return _default_reader
