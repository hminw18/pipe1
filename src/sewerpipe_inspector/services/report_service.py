from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


GRADE_FILL = {
    "대": PatternFill(fill_type="solid", fgColor="FF7F7F"),
    "중": PatternFill(fill_type="solid", fgColor="FFA500"),
    "소": PatternFill(fill_type="solid", fgColor="90EE90"),
}

HEADER_FILL = PatternFill(fill_type="solid", fgColor="E8EEF7")
LABEL_FILL = PatternFill(fill_type="solid", fgColor="F2F2F2")


class DefectReportRow(TypedDict):
    timestamp_ms: int
    drive_direction: str
    distance_m: float | None
    item_category: str | None
    condition_item: str | None
    defect_item: str | None
    grade: str | None
    quadrant: str | None
    manhole_defect_depth_m: float | None
    memo: str | None
    image_path: str


def _format_hms(timestamp_ms: int) -> str:
    seconds = timestamp_ms // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _value(row: Any, key: str, default: str = "") -> Any:
    if row is None:
        return default
    try:
        value = row[key]
    except Exception:
        value = getattr(row, key, default)
    return default if value is None else value


def _append_title(ws, title: str, row_index: int, width: int = 4) -> int:
    ws.cell(row=row_index, column=1, value=title)
    ws.merge_cells(
        start_row=row_index,
        start_column=1,
        end_row=row_index,
        end_column=width,
    )
    cell = ws.cell(row=row_index, column=1)
    cell.font = Font(bold=True, size=13)
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal="center")
    return row_index + 1


def _append_pair_table(ws, row_index: int, pairs: list[tuple[str, Any]]) -> int:
    for idx in range(0, len(pairs), 2):
        left = pairs[idx]
        right = pairs[idx + 1] if idx + 1 < len(pairs) else ("", "")
        values = [left[0], left[1], right[0], right[1]]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row_index, column=col, value=value)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if col in (1, 3):
                cell.fill = LABEL_FILL
                cell.font = Font(bold=True)
        row_index += 1
    return row_index + 1


class ReportService:
    def generate_inspection_report(
        self,
        report_path: Path,
        context,
        report,
        pipe_info,
        upstream_manhole,
        downstream_manhole,
        actual_survey,
        defects: list[DefectReportRow],
    ) -> Path:
        wb = Workbook()
        ws_basic = wb.active
        if ws_basic is None:
            raise RuntimeError("Workbook initialization failed")
        ws_basic.title = "기본정보"
        self._build_basic_sheet(ws_basic, context, report)

        ws_pipe = wb.create_sheet("관로조사정보")
        self._build_pipe_sheet(
            ws_pipe,
            pipe_info,
            upstream_manhole,
            downstream_manhole,
            actual_survey,
        )

        ws_defects = wb.create_sheet("결함목록")
        self._build_defect_sheet(ws_defects, defects)

        report_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(report_path)
        return report_path

    def _build_basic_sheet(self, ws, context, report) -> None:
        ws.column_dimensions[get_column_letter(1)].width = 18
        ws.column_dimensions[get_column_letter(2)].width = 28
        ws.column_dimensions[get_column_letter(3)].width = 18
        ws.column_dimensions[get_column_letter(4)].width = 28

        row = 1
        row = _append_title(ws, "프로젝트/사업 요약", row)
        row = _append_pair_table(
            ws,
            row,
            [
                ("프로젝트명", _value(context, "project_name")),
                ("프로젝트 생성일", _value(context, "project_created_at")),
                ("사업코드", _value(context, "business_code")),
                ("사업명", _value(context, "business_name")),
                ("발주처", _value(context, "client")),
                ("사업시작일", _value(context, "business_start_date")),
                ("사업완료일", _value(context, "business_end_date")),
            ],
        )
        row = _append_title(ws, "보고서 기본정보", row)
        row = _append_pair_table(
            ws,
            row,
            [
                ("보고서번호", _value(report, "report_number")),
                ("관로번호", _value(report, "pipe_number")),
                ("조사목적", _value(report, "survey_purpose")),
                ("조사일자", _value(report, "survey_date")),
                ("매설년수(년)", _value(report, "buried_years")),
                ("조사자", _value(report, "inspector")),
                ("시공자", _value(report, "contractor")),
            ],
        )
        row = _append_title(ws, "위치/배수 정보", row)
        row = _append_pair_table(
            ws,
            row,
            [
                ("처리구역", _value(report, "treatment_area")),
                ("배수구역", _value(report, "drainage_area")),
                ("배수분구", _value(report, "drainage_district")),
                ("배수방식", _value(report, "drain_type")),
                ("배수체계", _value(report, "drain_system")),
                ("시도", _value(report, "province")),
                ("시군구", _value(report, "city_county")),
                ("읍면동", _value(report, "town")),
                ("리", _value(report, "village")),
                ("지번", _value(report, "lot_number")),
                ("도로명주소", _value(report, "road_address")),
            ],
        )
        row = _append_title(ws, "관로 분류 정보", row)
        row = _append_pair_table(
            ws,
            row,
            [
                ("관종", _value(report, "pipe_type")),
                ("구분", _value(report, "category")),
                ("규격", _value(report, "specification")),
                ("보고서 생성일", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ],
        )

    def _build_pipe_sheet(
        self, ws, pipe_info, upstream_manhole, downstream_manhole, actual_survey
    ) -> None:
        headers = [
            "구분",
            "맨홀번호",
            "맨홀종류",
            "맨홀 내부재질",
            "맨홀 뚜껑재질",
            "맨홀 E.L(m)",
            "맨홀크기",
            "맨홀깊이(m)",
            "맨홀 인버트",
            "사다리모양",
            "위도",
            "경도",
        ]
        for idx, header in enumerate(headers, start=1):
            ws.cell(row=1, column=idx, value=header)
            ws.cell(row=1, column=idx).font = Font(bold=True)
            ws.cell(row=1, column=idx).fill = HEADER_FILL
            ws.column_dimensions[get_column_letter(idx)].width = 16

        for row_index, label, manhole in (
            (2, "상류맨홀", upstream_manhole),
            (3, "하류맨홀", downstream_manhole),
        ):
            values = [
                label,
                _value(manhole, "manhole_number"),
                _value(manhole, "manhole_type"),
                _value(manhole, "internal_material"),
                _value(manhole, "cover_material"),
                _value(manhole, "el_m"),
                _value(manhole, "size"),
                _value(manhole, "depth_m"),
                _value(manhole, "invert"),
                _value(manhole, "ladder_shape"),
                _value(manhole, "latitude"),
                _value(manhole, "longitude"),
            ]
            for col, value in enumerate(values, start=1):
                ws.cell(row=row_index, column=col, value=value)

        completed = "완주" if int(_value(pipe_info, "is_completed", 0) or 0) else "미완주"
        pipe_summary = (
            f"관로정보: 연장(m) {_value(pipe_info, 'length_m')} / "
            f"총주행거리(m) {_value(pipe_info, 'total_drive_distance_m')} / "
            f"완주여부 {completed} / "
            f"미주행거리(m) {_value(pipe_info, 'undriven_distance_m')}"
        )
        ws.cell(row=4, column=1, value=pipe_summary)
        ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=len(headers))
        ws.cell(row=4, column=1).fill = LABEL_FILL
        ws.cell(row=4, column=1).font = Font(bold=True)

        row = 6
        survey_headers = [
            "주행방향->맨홀번호",
            "발생지점(m)",
            "미주행사유",
            "미주행사유설명",
        ]
        for col, header in enumerate(survey_headers, start=1):
            ws.cell(row=row, column=col, value=header)
            ws.cell(row=row, column=col).font = Font(bold=True)
            ws.cell(row=row, column=col).fill = HEADER_FILL

        upstream_no = _value(upstream_manhole, "manhole_number")
        downstream_no = _value(downstream_manhole, "manhole_number")
        rows = [
            [
                f"시작->끝 / {upstream_no} -> {downstream_no}",
                _value(actual_survey, "start_occurrence_point_m"),
                _value(actual_survey, "start_undriven_reason", "없음"),
                _value(actual_survey, "start_undriven_reason_detail"),
            ],
            [
                f"끝->시작 / {downstream_no} -> {upstream_no}",
                _value(actual_survey, "end_occurrence_point_m"),
                _value(actual_survey, "end_undriven_reason", "없음"),
                _value(actual_survey, "end_undriven_reason_detail"),
            ],
        ]
        for offset, values in enumerate(rows, start=1):
            for col, value in enumerate(values, start=1):
                ws.cell(row=row + offset, column=col, value=value)

        content_row = row + 3
        ws.cell(
            row=content_row,
            column=1,
            value=f"조사내용: {_value(actual_survey, 'survey_content')}",
        )
        ws.merge_cells(start_row=content_row, start_column=1, end_row=content_row, end_column=4)
        ws.cell(row=content_row, column=1).alignment = Alignment(wrap_text=True)

    def _build_defect_sheet(
        self, ws, defects: list[DefectReportRow]
    ) -> None:
        headers = [
            "No",
            "시각",
            "주행방향",
            "거리(m)",
            "항목구분",
            "상태항목",
            "이상항목",
            "등급",
            "사분면",
            "맨홀결함깊이(m)",
            "메모",
            "이미지",
        ]
        widths = [8, 12, 12, 10, 12, 20, 20, 8, 16, 16, 28, 22]
        for idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=idx, value=header)
            cell.font = Font(bold=True)
            cell.fill = HEADER_FILL
            ws.column_dimensions[get_column_letter(idx)].width = widths[idx - 1]

        for index, defect in enumerate(defects, start=1):
            row_index = index + 1
            grade = defect.get("grade") or ""
            values = [
                index,
                _format_hms(int(defect["timestamp_ms"])),
                defect.get("drive_direction") or "",
                defect.get("distance_m") if defect.get("distance_m") is not None else "",
                defect.get("item_category") or "",
                defect.get("condition_item") or "",
                defect.get("defect_item") or "",
                grade,
                defect.get("quadrant") or "",
                defect.get("manhole_defect_depth_m")
                if defect.get("manhole_defect_depth_m") is not None
                else "",
                defect.get("memo") or "",
                "",
            ]
            for col, value in enumerate(values, start=1):
                ws.cell(row=row_index, column=col, value=value)
            if grade in GRADE_FILL:
                ws.cell(row=row_index, column=8).fill = GRADE_FILL[grade]

            image_path = Path(defect["image_path"])
            if image_path.exists():
                image = XLImage(str(image_path))
                image.width = 120
                image.height = 68
                ws.add_image(image, f"L{row_index}")
                ws.row_dimensions[row_index].height = 54
