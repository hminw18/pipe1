from pathlib import Path

from sewerpipe_inspector.db import Database
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.report_service import ReportService
from sewerpipe_inspector.services.storage_service import StorageService


def _make_inspection(tmp_path: Path) -> tuple[Database, StorageService, InspectionService]:
    db = Database(tmp_path / "app.db")
    storage = StorageService(tmp_path / "workspace")
    inspection = InspectionService(db, storage, ReportService())
    return db, storage, inspection


def _make_report_with_capture(
    db: Database,
    storage: StorageService,
    project_name: str = "Project-1",
    business_code: str = "B001",
    business_name: str = "Business-1",
    report_number: str = "R001",
    pipe_number: str = "PIPE-001",
) -> tuple[int, int, int, Path]:
    project_id = db.create_project(project_name)
    business_id = db.create_business(
        project_id, business_code, business_name, None, None, None
    )
    report_id = db.create_report(business_id, report_number, pipe_number)
    video_id = db.upsert_video(report_id, "/tmp/video.mp4", 10.0, None, "순주행")
    capture_path = storage.capture_path(
        project_name,
        business_code,
        business_name,
        report_number,
        pipe_number,
        "video.mp4",
        1000,
    )
    capture_path.write_bytes(b"capture")
    db.create_defect(
        report_id,
        video_id,
        1000,
        str(capture_path),
        "순주행",
        1.0,
        "관로",
        "구조특징",
        "균열(길이)",
        "중",
        "상",
        None,
        None,
    )
    return project_id, business_id, report_id, capture_path


def test_delete_report_with_artifacts_removes_report_directory(tmp_path: Path) -> None:
    db, storage, inspection = _make_inspection(tmp_path)
    _project_id, _business_id, report_id, capture_path = _make_report_with_capture(
        db, storage
    )
    report_root = capture_path.parent.parent

    assert report_root.exists()

    inspection.delete_report_with_artifacts(report_id)

    assert db.get_video(report_id) is None
    assert db.list_defects(report_id) == []
    assert not report_root.exists()


def test_delete_business_with_artifacts_removes_business_directory(tmp_path: Path) -> None:
    db, storage, inspection = _make_inspection(tmp_path)
    _project_id, business_id, report_id, capture_path = _make_report_with_capture(
        db, storage
    )
    business_root = capture_path.parent.parent.parent

    assert business_root.exists()

    inspection.delete_business_with_artifacts(business_id)

    assert db.list_reports(business_id) == []
    assert db.get_video(report_id) is None
    assert not business_root.exists()


def test_delete_project_with_artifacts_removes_project_directory(tmp_path: Path) -> None:
    db, storage, inspection = _make_inspection(tmp_path)
    project_id, business_id, report_id, capture_path = _make_report_with_capture(
        db, storage
    )
    project_root = capture_path.parent.parent.parent.parent

    assert project_root.exists()

    inspection.delete_project_with_artifacts(project_id)

    assert db.list_businesses(project_id) == []
    assert db.list_reports(business_id) == []
    assert db.get_video(report_id) is None
    assert not project_root.exists()
