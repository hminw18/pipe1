from pathlib import Path

import cv2
import numpy as np
import pytest
from pipe1_core.stop_detection.text_reader import (
    TextCounterReader,
    default_depth_reader,
    parse_distance,
)

from pipe1_server import media

# Counter regions cut from a real inspection video ("013.50m" style), named by their true value:
# white and light backgrounds, smudged and blurred digits, 7s and 3s once misread as 9s and 8s,
# and noise once read as a minus sign.
REAL_COUNTERS = sorted((Path(__file__).parent / "data" / "depth_counter").glob("*.png"))


def _counter_image(text: str, font: int, size: tuple[int, int] = (260, 60)) -> np.ndarray:
    width, height = size
    image = np.full((height, width, 3), (70, 110, 90), dtype=np.uint8)
    scale = 2.6 if font == cv2.FONT_HERSHEY_PLAIN else 1.4
    cv2.putText(image, text, (8, 44), font, scale, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(image, text, (8, 44), font, scale, (255, 255, 255), 2, cv2.LINE_AA)
    return image


def test_server_reads_the_counter_with_the_text_recognizer() -> None:
    assert isinstance(default_depth_reader(), TextCounterReader)


@pytest.mark.parametrize("path", REAL_COUNTERS, ids=lambda path: path.stem)
def test_reads_real_counter(path: Path) -> None:
    result = default_depth_reader().read_depth_value(cv2.imread(str(path)))

    assert result is not None
    assert result.value == float(path.stem.removesuffix("m"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12.3", 12.3), ("013.50m", 13.5), ("128.0 M", 128.0), ("0045.7m", 45.7)],
)
@pytest.mark.parametrize("font", [cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_PLAIN])
def test_reads_other_counter_formats(text: str, expected: float, font: int) -> None:
    result = default_depth_reader().read_depth_value(_counter_image(text, font))

    assert result is not None
    assert result.value == expected


@pytest.mark.parametrize("text", ["", "m", "1.2.3m", "12m3", "1.0m.5"])
def test_text_that_is_not_one_number_is_not_a_distance(text: str) -> None:
    assert parse_distance(text) is None


def test_capture_distance_is_read_from_the_counter_region() -> None:
    frame = np.full((720, 1280, 3), 140, dtype=np.uint8)
    frame[28:88, 53:313] = _counter_image("013.50m", cv2.FONT_HERSHEY_SIMPLEX)
    ok, png = cv2.imencode(".png", frame)
    assert ok

    assert media.read_distance(png.tobytes(), (53, 28, 260, 60)) == 13.5
