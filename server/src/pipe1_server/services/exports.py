"""Build report data from the database and render Excel/PDF with the shared core generators.

The payload shapes match what the desktop InspectionService passes to pipe1_core, so both
apps produce identical files.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipe1_core.resources import find_resource
from pipe1_core.services.pdf_report_service import generate_pipe_pdf_report
from pipe1_core.services.report_service import ReportService
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipe1_server.models.inspection import (
    ActualSurveyInformation,
    Business,
    InspectionProject,
    Manhole,
    PipeInformation,
    Report,
    ReportDefect,
    ReportGroup,
)
from pipe1_server.services.reports import (
    ACTUAL_SURVEY_FIELDS,
    MANHOLE_FIELDS,
    REPORT_VERSION_FIELDS,
)
from pipe1_server.storage import ObjectStore

PIPE_FIELDS = ("length_m", "total_drive_distance_m", "is_completed", "undriven_distance_m")
CONTEXT_FIELDS = (
    "project_name",
    "business_code",
    "business_name",
    "client",
    "business_start_date",
    "business_end_date",
)
PDF_REPORT_TYPES = ("inspection", "post_repair", "comparison")


def _row(model_row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: getattr(model_row, field) for field in fields} if model_row is not None else {}


@dataclass
class ReportData:
    context: dict[str, Any]
    report: dict[str, Any]
    pipe_info: dict[str, Any]
    upstream: dict[str, Any]
    downstream: dict[str, Any]
    actual: dict[str, Any]
    defects: list[ReportDefect]


def load_report_data(db: Session, report_id: uuid.UUID) -> ReportData:
    report = db.get(Report, report_id)
    if report is None:
        raise LookupError("report not found")
    group = db.get(ReportGroup, report.report_group_id)
    business = db.get(Business, group.business_id) if group else None
    project = db.get(InspectionProject, report.project_id)
    assert group is not None and business is not None and project is not None
    report_dict = {
        **_row(report, REPORT_VERSION_FIELDS),
        "report_number": group.report_number,
        "pipe_number": group.pipe_number,
        "version_name": report.version_name,
        "version_number": report.version_number,
    }
    context = {
        **report_dict,
        "project_name": project.project_name,
        **{field: getattr(business, field) for field in CONTEXT_FIELDS[1:]},
    }
    manholes = {
        m.role: _row(m, MANHOLE_FIELDS)
        for m in db.scalars(select(Manhole).where(Manhole.report_id == report.id))
    }
    pipe = db.scalar(select(PipeInformation).where(PipeInformation.report_id == report.id))
    survey = db.scalar(
        select(ActualSurveyInformation).where(ActualSurveyInformation.report_id == report.id)
    )
    defects = list(
        db.scalars(
            select(ReportDefect)
            .where(ReportDefect.report_id == report.id)
            .order_by(ReportDefect.timestamp_ms, ReportDefect.id)
        )
    )
    return ReportData(
        context=context,
        report=report_dict,
        pipe_info=_row(pipe, PIPE_FIELDS),
        upstream=manholes.get("upstream", {}),
        downstream=manholes.get("downstream", {}),
        actual=_row(survey, ACTUAL_SURVEY_FIELDS),
        defects=defects,
    )


def download_captures(
    store: ObjectStore, defects: list[ReportDefect], directory: Path
) -> dict[uuid.UUID, str]:
    """Local copies of capture images; missing objects are skipped like missing files."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[uuid.UUID, str] = {}
    for defect in defects:
        target = directory / f"{defect.id}.png"
        try:
            store.download_to(defect.image_object_key, target)
        except Exception:  # noqa: BLE001 - a missing image must not fail the export
            continue
        paths[defect.id] = str(target)
    return paths


def _excel_defects(data: ReportData, images: dict[uuid.UUID, str]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp_ms": d.timestamp_ms,
            "drive_direction": d.drive_direction,
            "distance_m": d.distance_m,
            "item_category": d.item_category,
            "condition_item": d.condition_item,
            "defect_item": d.defect_item,
            "grade": d.grade,
            "quadrant": d.quadrant,
            "manhole_defect_depth_m": d.manhole_defect_depth_m,
            "memo": d.memo,
            "image_path": images.get(d.id, ""),
        }
        for d in data.defects
    ]


def excel_filename(data: ReportData) -> str:
    return f"{data.report['report_number']}_InspectionReport.xlsx"


def render_excel(data: ReportData, images: dict[uuid.UUID, str], output: Path) -> Path:
    return ReportService().generate_inspection_report(
        report_path=output,
        context=data.context,
        report=data.report,
        pipe_info=data.pipe_info,
        upstream_manhole=data.upstream,
        downstream_manhole=data.downstream,
        actual_survey=data.actual,
        defects=_excel_defects(data, images),
    )


def workbook_data(data: ReportData, images: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "context": data.context,
        "report": data.report,
        "pipe_info": data.pipe_info,
        "upstream_manhole": data.upstream,
        "downstream_manhole": data.downstream,
        "actual_survey": data.actual,
        "defects": _excel_defects(data, images),
    }


def render_business_workbook(
    kind: str,
    reports: list[tuple[ReportData, dict[uuid.UUID, str]]],
    output: Path,
    include_photos: bool,
) -> Path:
    service = ReportService()
    business_context = reports[0][0].context
    payload = [workbook_data(data, images) for data, images in reports]
    if kind == "internal":
        return service.generate_internal_defect_workbook(
            report_path=output,
            business_context=business_context,
            reports=payload,
            include_photos=include_photos,
        )
    return service.generate_defect_aggregate_workbook(
        report_path=output, business_context=business_context, reports=payload
    )


def _pdf_defects(data: ReportData, images: dict[uuid.UUID, str]) -> tuple[list[dict], float]:
    rows, max_distance = [], 0.0
    for d in data.defects:
        distance = abs(float(d.distance_m)) if d.distance_m is not None else 0.0
        max_distance = max(max_distance, distance)
        rows.append(
            {
                "distance_m": distance,
                "drive_direction": d.drive_direction or "",
                "item_category": d.item_category or "",
                "grade": d.grade or "",
                "image_path": images.get(d.id, ""),
                "condition_item": d.condition_item or "",
                "defect_item": d.defect_item or "",
                "timestamp_ms": int(d.timestamp_ms),
            }
        )
    return rows, max_distance


def render_pdf(
    before: ReportData,
    before_images: dict[uuid.UUID, str],
    after: tuple[ReportData, dict[uuid.UUID, str]] | None,
    report_type: str,
    output_dir: Path,
) -> Path:
    pipe_png = find_resource("pipe.png")
    if pipe_png is None:
        raise RuntimeError("pipe.png template not found")
    defects_before, before_max = _pdf_defects(before, before_images)
    defects_after, after_max = _pdf_defects(*after) if after else ([], 0.0)
    pipe_length = float(before.pipe_info.get("length_m") or 0.0)
    if pipe_length <= 0:
        pipe_length = max(before_max, after_max)
    return Path(
        generate_pipe_pdf_report(
            project_name=before.context["project_name"],
            zone_name=before.context["business_name"],
            pipe_code=before.report["report_number"],
            pipe_length_m=pipe_length,
            pipe_png_path=str(pipe_png),
            defects_before=defects_before,
            defects_after=defects_after,
            output_path=str(output_dir),
            report_type=report_type,
            report_context=before.context,
            pipe_info=before.pipe_info,
            upstream_manhole=before.upstream,
            downstream_manhole=before.downstream,
            actual_survey=before.actual,
        )
    )


def comparison_mismatches(before: ReportData, after: ReportData) -> list[str]:
    """Fields that must match for an AFTER merge; report_number is exempt
    (Product_Requirements §6.2)."""
    differences: list[str] = []

    def compare(section: str, left: dict[str, Any], right: dict[str, Any], skip=()) -> None:
        for field in left.keys() | right.keys():
            if field in skip:
                continue
            a, b = left.get(field), right.get(field)
            if (a in (None, "")) and (b in (None, "")):
                continue
            if str(a).strip() != str(b).strip():
                differences.append(f"{section}.{field}")

    compare(
        "context",
        before.context,
        after.context,
        skip=("report_number", "version_name", "version_number") + REPORT_VERSION_FIELDS,
    )
    compare(
        "report",
        before.report,
        after.report,
        skip=("report_number", "version_name", "version_number"),
    )
    compare("pipe_information", before.pipe_info, after.pipe_info)
    compare("upstream_manhole", before.upstream, after.upstream)
    compare("downstream_manhole", before.downstream, after.downstream)
    compare("actual_survey", before.actual, after.actual)
    return sorted(differences)
