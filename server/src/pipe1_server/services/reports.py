"""Report creation, versioning, duplication and export readiness.

Ported from the desktop db.py (create_report_version_from_latest, duplicate_*), with the
web storage layout: a new version shares the report group's video and gets its own copy of
every capture image (one object per defect).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pipe1_server.ids import uuid7
from pipe1_server.models.inspection import (
    ActualSurveyInformation,
    Business,
    DefectSuggestion,
    InspectionProject,
    Manhole,
    PipeInformation,
    Report,
    ReportDefect,
    ReportGroup,
    ReportVideo,
)
from pipe1_server.storage import ObjectStore, report_prefix

REPORT_VERSION_FIELDS = (
    "survey_purpose",
    "survey_date",
    "buried_years",
    "inspector",
    "contractor",
    "treatment_area",
    "drainage_area",
    "drainage_district",
    "drain_type",
    "drain_system",
    "province",
    "city_county",
    "town",
    "village",
    "lot_number",
    "road_address",
    "pipe_type",
    "category",
    "specification",
)
MANHOLE_FIELDS = (
    "manhole_number",
    "manhole_type",
    "internal_material",
    "cover_material",
    "el_m",
    "size",
    "depth_m",
    "invert",
    "ladder_shape",
    "latitude",
    "longitude",
)
ACTUAL_SURVEY_FIELDS = (
    "start_occurrence_point_m",
    "start_undriven_reason",
    "start_undriven_reason_detail",
    "end_occurrence_point_m",
    "end_undriven_reason",
    "end_undriven_reason_detail",
    "survey_content",
)
DEFECT_COPY_FIELDS = (
    "timestamp_ms",
    "drive_direction",
    "distance_m",
    "item_category",
    "condition_item",
    "defect_item",
    "grade",
    "quadrant",
    "manhole_defect_depth_m",
    "memo",
    "created_by",
)
SUGGESTION_COPY_FIELDS = (
    "model_version",
    "code",
    "item_category",
    "defect_item",
    "confidence",
    "start_ms",
    "end_ms",
    "best_ms",
    "best_box",
    "distance_m",
    "boxes",
    "status",
)
ROLES = ("upstream", "downstream")
COPY_SUFFIX = " 복사본"


def capture_key(project: InspectionProject, report_id: uuid.UUID, defect_id: uuid.UUID) -> str:
    return (
        f"{report_prefix(project.organization_id, project.id, report_id)}captures/{defect_id}.png"
    )


def apply_pipe_derivations(pipe: PipeInformation) -> None:
    """undriven = length - driven; completed when they are equal (Product_Requirements §4.4)."""
    if pipe.length_m is not None and pipe.total_drive_distance_m is not None:
        pipe.undriven_distance_m = round(pipe.length_m - pipe.total_drive_distance_m, 6)
        pipe.is_completed = abs(pipe.undriven_distance_m) < 1e-9
    else:
        pipe.undriven_distance_m = None
        pipe.is_completed = False


def _add_report_children(db: Session, report: Report) -> None:
    manholes = {
        role: Manhole(id=uuid7(), project_id=report.project_id, report_id=report.id, role=role)
        for role in ROLES
    }
    db.add_all(manholes.values())
    db.flush()
    db.add(
        PipeInformation(
            project_id=report.project_id,
            report_id=report.id,
            upstream_manhole_id=manholes["upstream"].id,
            downstream_manhole_id=manholes["downstream"].id,
        )
    )
    db.add(ActualSurveyInformation(project_id=report.project_id, report_id=report.id))
    db.flush()


def create_report_group(
    db: Session, business: Business, report_number: str, pipe_number: str
) -> Report:
    group = ReportGroup(
        project_id=business.project_id,
        business_id=business.id,
        report_number=report_number,
        pipe_number=pipe_number,
    )
    db.add(group)
    db.flush()
    report = Report(
        project_id=business.project_id,
        report_group_id=group.id,
        version_number=1,
        version_name="v1",
    )
    db.add(report)
    db.flush()
    _add_report_children(db, report)
    return report


def _copy_inputs(db: Session, source: Report, target: Report) -> None:
    for field in REPORT_VERSION_FIELDS:
        setattr(target, field, getattr(source, field))
    for role in ROLES:
        src = db.scalar(select(Manhole).where(Manhole.report_id == source.id, Manhole.role == role))
        dst = db.scalar(select(Manhole).where(Manhole.report_id == target.id, Manhole.role == role))
        if src is not None and dst is not None:
            for field in MANHOLE_FIELDS:
                setattr(dst, field, getattr(src, field))
    src_pipe = db.scalar(select(PipeInformation).where(PipeInformation.report_id == source.id))
    dst_pipe = db.scalar(select(PipeInformation).where(PipeInformation.report_id == target.id))
    if src_pipe is not None and dst_pipe is not None:
        dst_pipe.length_m = src_pipe.length_m
        dst_pipe.total_drive_distance_m = src_pipe.total_drive_distance_m
        apply_pipe_derivations(dst_pipe)
    src_survey = db.scalar(
        select(ActualSurveyInformation).where(ActualSurveyInformation.report_id == source.id)
    )
    dst_survey = db.scalar(
        select(ActualSurveyInformation).where(ActualSurveyInformation.report_id == target.id)
    )
    if src_survey is not None and dst_survey is not None:
        for field in ACTUAL_SURVEY_FIELDS:
            setattr(dst_survey, field, getattr(src_survey, field))
    db.flush()


def latest_version(db: Session, group_id: uuid.UUID) -> Report | None:
    return db.scalar(
        select(Report)
        .where(Report.report_group_id == group_id)
        .order_by(Report.version_number.desc())
        .limit(1)
    )


def create_version_from_latest(
    db: Session, group: ReportGroup, project: InspectionProject, store: ObjectStore
) -> Report:
    latest = latest_version(db, group.id)
    if latest is None:
        raise ValueError("보고서 버전을 찾을 수 없습니다")
    number = latest.version_number + 1
    report = Report(
        project_id=group.project_id,
        report_group_id=group.id,
        version_number=number,
        version_name=f"v{number}",
    )
    db.add(report)
    db.flush()
    _add_report_children(db, report)
    _copy_inputs(db, latest, report)

    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == latest.id))
    if video is None:
        return report
    new_video = ReportVideo(
        project_id=report.project_id,
        report_id=report.id,
        **{
            field: getattr(video, field)
            for field in (
                "original_object_key",
                "original_filename",
                "size_bytes",
                "playback_object_key",
                "transcode_status",
                "duration_seconds",
                "fps",
                "width",
                "height",
                "recorded_date",
                "scan_direction",
                "depth_roi_x",
                "depth_roi_y",
                "depth_roi_w",
                "depth_roi_h",
                "travel_start_s",
            )
        },
    )
    db.add(new_video)
    db.flush()
    new_defect_ids: dict[uuid.UUID, uuid.UUID] = {}
    for defect in db.scalars(select(ReportDefect).where(ReportDefect.report_id == latest.id)):
        defect_id = uuid7()
        new_defect_ids[defect.id] = defect_id
        key = capture_key(project, report.id, defect_id)
        store.copy(defect.image_object_key, key)
        db.add(
            ReportDefect(
                id=defect_id,
                project_id=report.project_id,
                report_id=report.id,
                video_id=new_video.id,
                image_object_key=key,
                **{field: getattr(defect, field) for field in DEFECT_COPY_FIELDS},
            )
        )
    db.flush()
    for suggestion in db.scalars(
        select(DefectSuggestion).where(DefectSuggestion.report_id == latest.id)
    ):
        confirmed = suggestion.defect_id and new_defect_ids.get(suggestion.defect_id)
        db.add(
            DefectSuggestion(
                project_id=report.project_id,
                report_id=report.id,
                video_id=new_video.id,
                defect_id=confirmed,
                **{field: getattr(suggestion, field) for field in SUGGESTION_COPY_FIELDS},
            )
        )
    db.flush()
    return report


def _copy_name(db: Session, model, column, scope_column, scope_value, base: str) -> str:
    candidate = f"{base}{COPY_SUFFIX}"
    suffix = 2
    while db.scalar(
        select(func.count())
        .select_from(model)
        .where(scope_column == scope_value, column == candidate)
    ):
        candidate = f"{base}{COPY_SUFFIX} {suffix}"
        suffix += 1
    return candidate


def duplicate_report_group(db: Session, group: ReportGroup, target: Business) -> Report:
    """Copy the latest version's inputs into a new report; video and defects are not copied."""
    source = latest_version(db, group.id)
    if source is None:
        raise ValueError("보고서 버전을 찾을 수 없습니다")
    number = group.report_number
    if db.scalar(
        select(func.count())
        .select_from(ReportGroup)
        .where(ReportGroup.business_id == target.id, ReportGroup.report_number == number)
    ):
        number = _copy_name(
            db, ReportGroup, ReportGroup.report_number, ReportGroup.business_id, target.id, number
        )
    report = create_report_group(db, target, number, group.pipe_number)
    _copy_inputs(db, source, report)
    return report


def duplicate_business(db: Session, business: Business, target_project_id: uuid.UUID) -> Business:
    code = business.business_code
    if db.scalar(
        select(func.count())
        .select_from(Business)
        .where(Business.project_id == target_project_id, Business.business_code == code)
    ):
        code = _copy_name(
            db, Business, Business.business_code, Business.project_id, target_project_id, code
        )
    copy = Business(
        project_id=target_project_id,
        business_code=code,
        business_name=business.business_name
        if target_project_id != business.project_id
        else f"{business.business_name}{COPY_SUFFIX}",
        client=business.client,
        business_start_date=business.business_start_date,
        business_end_date=business.business_end_date,
    )
    db.add(copy)
    db.flush()
    for group in db.scalars(
        select(ReportGroup)
        .where(ReportGroup.business_id == business.id)
        .order_by(ReportGroup.created_at)
    ):
        duplicate_report_group(db, group, copy)
    return copy


def duplicate_project(
    db: Session, project: InspectionProject, owner_user_id: uuid.UUID, visibility: str
) -> InspectionProject:
    copy = InspectionProject(
        organization_id=project.organization_id,
        owner_user_id=owner_user_id,
        visibility=visibility,
        project_name=f"{project.project_name}{COPY_SUFFIX}",
    )
    db.add(copy)
    db.flush()
    for business in db.scalars(
        select(Business).where(Business.project_id == project.id).order_by(Business.created_at)
    ):
        duplicate_business(db, business, copy.id)
    return copy


@dataclass(frozen=True)
class ExportReadiness:
    ready: bool
    missing: list[str]


def export_readiness(db: Session, report: Report, group: ReportGroup) -> ExportReadiness:
    """Initial required export field set (Product_Requirements §6.2)."""
    missing: list[str] = []
    for field, value in (
        ("report_number", group.report_number),
        ("pipe_number", group.pipe_number),
        ("survey_date", report.survey_date),
        ("buried_years", report.buried_years),
    ):
        if value is None or not str(value).strip():
            missing.append(field)
    pipe = db.scalar(select(PipeInformation).where(PipeInformation.report_id == report.id))
    if pipe is None or pipe.length_m is None:
        missing.append("length_m")
    if pipe is None or pipe.total_drive_distance_m is None:
        missing.append("total_drive_distance_m")
    roles = set(db.scalars(select(Manhole.role).where(Manhole.report_id == report.id)))
    for role in ROLES:
        if role not in roles:
            missing.append(f"{role}_manhole")
    return ExportReadiness(ready=not missing, missing=missing)
