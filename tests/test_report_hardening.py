"""Excel formula injection and report image size (docs/Web_Architecture.md §6)."""

from pathlib import Path

from openpyxl import load_workbook
from PIL import Image

from pipe1_core.services.report_service import ReportService
from pipe1_core.services.thumbnails import EXCEL_MAX_PIXELS, thumbnail_jpeg
from sewerpipe_inspector.db import Database
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.storage_service import StorageService

INJECTION = '=HYPERLINK("https://evil.example","click")'


def _large_capture(path: Path) -> Path:
    Image.new("RGB", (1920, 1080), (120, 60, 30)).save(path)
    return path


def _report_with_user_text(tmp_path: Path) -> tuple[InspectionService, int]:
    db = Database(tmp_path / "app.db")
    inspection = InspectionService(db, StorageService(tmp_path / "workspace"), ReportService())
    project_id = db.create_project("P")
    business_id = db.create_business(project_id, "B001", "B", INJECTION, None, None)
    report_id = db.create_report(business_id, "R001", "PIPE-001")
    report = db.get_report(report_id)
    payload = {field: report[field] for field in report.keys()}
    payload.update({"survey_date": "2026-06-13", "buried_years": "10", "inspector": INJECTION})
    db.update_report(report_id, payload)
    db.update_pipe_information(report_id, 20.0, 18.0)
    video_id = db.upsert_video(report_id, str(tmp_path / "a.mp4"), 10.0, None, "순주행")
    db.create_defect(
        report_id, video_id, 1200, str(_large_capture(tmp_path / "c.png")), "순주행", 1.25,
        "관로", None, "균열(길이)", "대", "상", None, INJECTION,
    )
    return inspection, report_id


def test_user_text_starting_with_equals_stays_text(tmp_path: Path) -> None:
    inspection, report_id = _report_with_user_text(tmp_path)
    workbook = load_workbook(inspection.generate_excel_report(report_id))
    injected = [
        cell
        for ws in workbook.worksheets
        for row in ws.iter_rows()
        for cell in row
        if cell.value == INJECTION
    ]
    assert injected, "user text should be present in the workbook"
    assert all(cell.data_type == "s" for cell in injected)


def test_report_formulas_still_work(tmp_path: Path) -> None:
    inspection, report_id = _report_with_user_text(tmp_path)
    path = inspection.generate_internal_defect_workbook(
        [report_id], tmp_path / "internal.xlsx", include_photos=True
    )
    workbook = load_workbook(path)
    formulas = [
        cell.value
        for ws in workbook.worksheets
        for row in ws.iter_rows()
        for cell in row
        if cell.data_type == "f"
    ]
    assert any(str(f).startswith("=MAX(") for f in formulas)
    assert INJECTION not in formulas


def test_embedded_images_are_downscaled(tmp_path: Path) -> None:
    capture = _large_capture(tmp_path / "big.png")
    thumbnail = Image.open(thumbnail_jpeg(capture, EXCEL_MAX_PIXELS))
    assert thumbnail.format == "JPEG"
    assert max(thumbnail.size) == EXCEL_MAX_PIXELS
    assert thumbnail.size[0] / thumbnail.size[1] == 1920 / 1080


def test_grades_are_not_colour_coded(tmp_path: Path) -> None:
    inspection, report_id = _report_with_user_text(tmp_path)
    sheet = load_workbook(inspection.generate_excel_report(report_id))["결함목록"]
    grade_cells = [cell for row in sheet.iter_rows() for cell in row if cell.value == "대"]
    assert grade_cells
    assert all(cell.fill.fill_type is None for cell in grade_cells)
