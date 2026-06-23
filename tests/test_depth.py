from pathlib import Path
from types import SimpleNamespace

import numpy as np

from sewerpipe_inspector.db import Database
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.ocr_service import OCRService
from sewerpipe_inspector.services.report_service import ReportService
from sewerpipe_inspector.services.storage_service import StorageService

class FakeDepthMatcher:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    def read_depth_value(self, roi_image, slots):
        self.calls.append((roi_image.shape, slots))
        return self.result


def test_distance_ocr_is_positive_for_report_video(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    storage = StorageService(tmp_path / "workspace")
    inspection = InspectionService(db, storage, ReportService())

    project_id = db.create_project("P1")
    business_id = db.create_business(project_id, "B001", "사업1", None, None, None)
    report_id = db.create_report(business_id, "R001", "PIPE-001")
    video_id = db.upsert_video(report_id, str(tmp_path / "a.mp4"), 10.0, None, "역주행")
    db.update_video_depth_roi(video_id, 0, 0, 100, 20)

    row = db.get_video_by_id(video_id)
    assert row is not None

    inspection.ocr.read_depth_value = lambda _frame, _roi: -3.45
    distance = inspection._extract_distance(row, object())
    assert distance == 3.45


def test_distance_ocr_uses_opencv_depth_digit_matcher() -> None:
    matcher = FakeDepthMatcher(
        SimpleNamespace(value=47.2, min_score=0.8, min_margin=0.1)
    )
    service = OCRService(matcher=matcher)
    frame = np.zeros((100, 200, 3), dtype=np.uint8)

    value = service.read_depth_value(frame, (10, 20, 149, 53))

    assert value == 47.2
    assert matcher.calls == [((53, 149, 3), None)]


def test_distance_ocr_falls_back_to_tesseract_on_low_confidence_match() -> None:
    matcher = FakeDepthMatcher(
        SimpleNamespace(value=47.2, min_score=0.49, min_margin=0.1)
    )
    service = OCRService(
        matcher=matcher,
        tesseract_reader=lambda _image, _config: "047.2m",
    )
    frame = np.zeros((100, 200, 3), dtype=np.uint8)

    assert service.read_depth_value(frame, (10, 20, 149, 53)) == 47.2


def test_distance_ocr_returns_none_when_both_readers_fail() -> None:
    matcher = FakeDepthMatcher(None)
    service = OCRService(
        matcher=matcher,
        tesseract_reader=lambda _image, _config: "",
    )
    frame = np.zeros((100, 200, 3), dtype=np.uint8)

    assert service.read_depth_value(frame, (10, 20, 149, 53)) is None
