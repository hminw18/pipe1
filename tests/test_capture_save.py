from pathlib import Path

import numpy as np

from sewerpipe_inspector.db import Database
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.report_service import ReportService
from sewerpipe_inspector.services.storage_service import StorageService


def test_capture_save_uses_unicode_safe_file_write(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "app.db")
    storage = StorageService(tmp_path / "작업공간")
    inspection = InspectionService(db, storage, ReportService())

    project_id = db.create_project("프로젝트")
    business_id = db.create_business(
        project_id, "사업001", "한글사업", None, None, None
    )
    report_id = db.create_report(business_id, "보고서001", "관로001")
    video_id = db.upsert_video(report_id, "영상.mp4", 10.0, None, "순주행")
    frame = np.zeros((24, 32, 3), dtype=np.uint8)

    def fail_imwrite(*_args, **_kwargs):
        raise AssertionError("cv2.imwrite should not be used for capture paths")

    monkeypatch.setattr(
        "sewerpipe_inspector.services.inspection_service.cv2.imwrite",
        fail_imwrite,
    )

    defect_id = inspection.capture_and_save_defect(
        report_id=report_id,
        video_id=video_id,
        video_file=Path("영상.mp4"),
        frame=frame,
        timestamp_ms=1234,
        drive_direction="순주행",
        distance_m=1.2,
        item_category="관로",
        condition_item="조사완료(순방향)",
        defect_item=None,
        grade=None,
        quadrant=None,
        manhole_defect_depth_m=None,
        memo=None,
    )

    defect = db.get_defect(defect_id)
    assert defect is not None
    capture_path = Path(defect["image_path"])
    assert capture_path.exists()
    assert "프로젝트" in str(capture_path)
    assert capture_path.read_bytes().startswith(b"\x89PNG")
