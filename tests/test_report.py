from pathlib import Path

from openpyxl import load_workbook

from sewerpipe_inspector.db import Database
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.report_service import ReportService
from sewerpipe_inspector.services.storage_service import StorageService


def test_generate_inspection_report_creates_multi_sheet_workbook(tmp_path: Path) -> None:
    capture = tmp_path / "capture.png"
    capture.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x1dc\xf8\xcf\xc0\x00\x00\x04\x01\x01\x00\x18\xdd\x8d\x17\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    db = Database(tmp_path / "app.db")
    storage = StorageService(tmp_path / "workspace")
    inspection = InspectionService(db, storage, ReportService())

    project_id = db.create_project("Project-1")
    business_id = db.create_business(
        project_id, "B001", "Business-1", "Client", "2026-01-01", "2026-12-31"
    )
    report_id = db.create_report(business_id, "R001", "PIPE-001")
    report = db.get_report(report_id)
    payload = {field: report[field] for field in report.keys()}
    payload.update({"survey_date": "2026-06-13", "buried_years": "10"})
    db.update_report(report_id, payload)
    db.update_pipe_information(report_id, 20.0, 18.0)
    db.update_manhole(report_id, "upstream", {"manhole_number": "U-1"})
    db.update_manhole(report_id, "downstream", {"manhole_number": "D-1"})
    db.update_actual_survey(
        report_id,
        {
            "start_occurrence_point_m": 1.0,
            "start_undriven_reason": "없음",
            "start_undriven_reason_detail": None,
            "end_occurrence_point_m": 2.0,
            "end_undriven_reason": "기타",
            "end_undriven_reason_detail": "memo",
            "survey_content": "조사내용",
        },
    )
    video_id = db.upsert_video(report_id, str(tmp_path / "a.mp4"), 10.0, None, "순주행")
    db.create_defect(
        report_id, video_id, 1200, str(capture), "순주행", 1.25,
        "관로", "구조특징", "균열(길이)", "대", "상", None, "minor"
    )

    output = inspection.generate_excel_report(report_id)

    assert output.exists()
    workbook = load_workbook(output)
    assert workbook.sheetnames == ["기본정보", "관로조사정보", "결함목록"]
    assert workbook["기본정보"]["A1"].value == "프로젝트/사업 요약"
    assert "관로정보:" in workbook["관로조사정보"]["A4"].value
    assert workbook["결함목록"]["F2"].value == "구조특징"
    assert workbook["결함목록"]["H2"].value == "대"
