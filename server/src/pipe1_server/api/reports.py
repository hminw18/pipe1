"""Reports, versions and their detail tables (docs/Product_Requirements.md §4.3-§4.6, §6.2).

All updates are partial (PATCH): fields not sent are left unchanged.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pipe1_core.manhole_options import MANHOLE_OPTIONS
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pipe1_server.api.deps import ClientIp, CurrentSession, DbSession, Store, UserAgent
from pipe1_server.api.projects import load_business
from pipe1_server.db import after_commit
from pipe1_server.models.auth import User
from pipe1_server.models.inspection import (
    ActualSurveyInformation,
    Business,
    InspectionProject,
    Manhole,
    PipeInformation,
    Report,
    ReportDefect,
    ReportGroup,
    ReportVideo,
)
from pipe1_server.security import audit
from pipe1_server.security.authorization import Action, authorize
from pipe1_server.services import reports as report_service
from pipe1_server.storage import ObjectStore, report_group_prefix, report_prefix

router = APIRouter(prefix="/api", tags=["reports"])

UndrivenReason = Literal[
    "없음",
    "맨홀뚜껑파손",
    "맨홀연결파손",
    "연결관돌출",
    "연결관접합부",
    "이음부",
    "침입수",
    "유출수",
    "부식",
    "관파속및크랙",
    "곡관로",
    "관침하",
    "관구배",
    "타관통과",
    "폐유",
    "모르타르",
    "토사퇴적",
    "개폐불가",
    "맨홀묻힘",
    "기타",
]
Text = str | None


class ReportCreate(BaseModel):
    report_number: str = Field(min_length=1, max_length=100)
    pipe_number: str = Field(min_length=1, max_length=100)


class ReportUpdate(BaseModel):
    report_number: str | None = Field(default=None, min_length=1, max_length=100)
    pipe_number: str | None = Field(default=None, min_length=1, max_length=100)
    version_name: str | None = Field(default=None, min_length=1, max_length=50)
    survey_purpose: Text = Field(default=None, max_length=500)
    survey_date: Text = Field(default=None, max_length=50)
    buried_years: Text = Field(default=None, max_length=50)
    inspector: Text = Field(default=None, max_length=200)
    contractor: Text = Field(default=None, max_length=200)
    treatment_area: Text = Field(default=None, max_length=200)
    drainage_area: Text = Field(default=None, max_length=200)
    drainage_district: Text = Field(default=None, max_length=200)
    drain_type: Text = Field(default=None, max_length=200)
    drain_system: Text = Field(default=None, max_length=200)
    province: Text = Field(default=None, max_length=100)
    city_county: Text = Field(default=None, max_length=100)
    town: Text = Field(default=None, max_length=100)
    village: Text = Field(default=None, max_length=100)
    lot_number: Text = Field(default=None, max_length=100)
    road_address: Text = Field(default=None, max_length=500)
    pipe_type: Text = Field(default=None, max_length=100)
    category: Text = Field(default=None, max_length=100)
    specification: Text = Field(default=None, max_length=100)


class PipeUpdate(BaseModel):
    length_m: float | None = Field(default=None, ge=0)
    total_drive_distance_m: float | None = Field(default=None, ge=0)


class ManholeUpdate(BaseModel):
    manhole_number: Text = Field(default=None, max_length=100)
    manhole_type: Text = Field(default=None, max_length=100)
    internal_material: Text = Field(default=None, max_length=100)
    cover_material: Text = Field(default=None, max_length=100)
    el_m: Text = Field(default=None, max_length=50)
    size: Text = Field(default=None, max_length=50)
    depth_m: Text = Field(default=None, max_length=50)
    invert: Text = Field(default=None, max_length=50)
    ladder_shape: Text = Field(default=None, max_length=100)
    latitude: Text = Field(default=None, max_length=50)
    longitude: Text = Field(default=None, max_length=50)

    @field_validator(*MANHOLE_OPTIONS)
    @classmethod
    def _one_of_the_choices(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and value not in MANHOLE_OPTIONS[info.field_name or ""]:
            raise ValueError("목록에 있는 값을 고르세요.")
        return value


class SurveyUpdate(BaseModel):
    start_occurrence_point_m: float | None = None
    start_undriven_reason: UndrivenReason | None = None
    start_undriven_reason_detail: Text = Field(default=None, max_length=500)
    end_occurrence_point_m: float | None = None
    end_undriven_reason: UndrivenReason | None = None
    end_undriven_reason_detail: Text = Field(default=None, max_length=500)
    survey_content: Text = Field(default=None, max_length=5000)


class DuplicateTarget(BaseModel):
    target_business_id: uuid.UUID | None = None


class VersionOut(BaseModel):
    id: uuid.UUID
    version_number: int
    version_name: str
    updated_at: datetime


class ReportSummary(BaseModel):
    report_group_id: uuid.UUID
    latest_report_id: uuid.UUID
    report_number: str
    pipe_number: str
    version_count: int
    updated_at: datetime


class ReportDetail(BaseModel):
    id: uuid.UUID
    report_group_id: uuid.UUID
    business_id: uuid.UUID
    project_id: uuid.UUID
    report_number: str
    pipe_number: str
    version_number: int
    version_name: str
    versions: list[VersionOut]
    fields: dict[str, str | None]
    pipe_information: dict[str, object]
    manholes: dict[str, dict[str, str | None]]
    actual_survey: dict[str, object]
    has_video: bool
    defect_count: int
    export_ready: bool
    export_missing: list[str]
    updated_at: datetime


def load_report(
    db: Session,
    user: User,
    report_id: uuid.UUID,
    action: Action,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Report, ReportGroup, InspectionProject]:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    project = authorize(db, user, report.project_id, action, ip=ip, user_agent=user_agent)
    group = db.get(ReportGroup, report.report_group_id)
    assert group is not None
    return report, group, project


def _load_group(
    db: Session, user: User, group_id: uuid.UUID, action: Action, ip, user_agent
) -> tuple[ReportGroup, InspectionProject]:
    group = db.get(ReportGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    project = authorize(db, user, group.project_id, action, ip=ip, user_agent=user_agent)
    return group, project


def _row_dict(row, fields) -> dict:
    return {field: getattr(row, field) for field in fields} if row is not None else {}


def report_detail(db: Session, report: Report, group: ReportGroup) -> ReportDetail:
    versions = db.scalars(
        select(Report).where(Report.report_group_id == group.id).order_by(Report.version_number)
    ).all()
    pipe = db.scalar(select(PipeInformation).where(PipeInformation.report_id == report.id))
    manholes = {
        m.role: _row_dict(m, report_service.MANHOLE_FIELDS)
        for m in db.scalars(select(Manhole).where(Manhole.report_id == report.id))
    }
    survey = db.scalar(
        select(ActualSurveyInformation).where(ActualSurveyInformation.report_id == report.id)
    )
    readiness = report_service.export_readiness(db, report, group)
    return ReportDetail(
        id=report.id,
        report_group_id=group.id,
        business_id=group.business_id,
        project_id=report.project_id,
        report_number=group.report_number,
        pipe_number=group.pipe_number,
        version_number=report.version_number,
        version_name=report.version_name,
        versions=[
            VersionOut(
                id=v.id,
                version_number=v.version_number,
                version_name=v.version_name,
                updated_at=v.updated_at,
            )
            for v in versions
        ],
        fields=_row_dict(report, report_service.REPORT_VERSION_FIELDS),
        pipe_information=_row_dict(
            pipe,
            ("length_m", "total_drive_distance_m", "is_completed", "undriven_distance_m"),
        ),
        manholes=manholes,
        actual_survey=_row_dict(survey, report_service.ACTUAL_SURVEY_FIELDS),
        has_video=db.scalar(select(ReportVideo.id).where(ReportVideo.report_id == report.id))
        is not None,
        defect_count=db.scalar(
            select(func.count())
            .select_from(ReportDefect)
            .where(ReportDefect.report_id == report.id)
        )
        or 0,
        export_ready=readiness.ready,
        export_missing=readiness.missing,
        updated_at=report.updated_at,
    )


def _touch(report: Report) -> None:
    report.updated_at = func.now()


def _unreferenced_video_keys(db: Session, keys: set[str]) -> list[str]:
    referenced = set(
        db.scalars(
            select(ReportVideo.original_object_key).where(ReportVideo.original_object_key.in_(keys))
        )
    ) | set(
        db.scalars(
            select(ReportVideo.playback_object_key).where(ReportVideo.playback_object_key.in_(keys))
        )
    )
    return sorted(keys - referenced)


def _schedule_deletes(
    db: Session, store: ObjectStore, prefixes: list[str], keys: list[str]
) -> None:
    for prefix in prefixes:
        after_commit(db, store.delete_prefix, prefix)
    for key in keys:
        after_commit(db, store.delete, key)


# --- Report groups ---------------------------------------------------------------------


@router.get("/businesses/{business_id}/reports", response_model=list[ReportSummary])
def list_reports(
    business_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> list[ReportSummary]:
    load_business(db, active.user, business_id, "view", ip, user_agent)
    summaries = []
    for group in db.scalars(
        select(ReportGroup)
        .where(ReportGroup.business_id == business_id)
        .order_by(ReportGroup.report_number, ReportGroup.pipe_number)
    ):
        latest = report_service.latest_version(db, group.id)
        if latest is None:
            continue
        count = db.scalar(
            select(func.count()).select_from(Report).where(Report.report_group_id == group.id)
        )
        summaries.append(
            ReportSummary(
                report_group_id=group.id,
                latest_report_id=latest.id,
                report_number=group.report_number,
                pipe_number=group.pipe_number,
                version_count=count or 0,
                updated_at=latest.updated_at,
            )
        )
    return summaries


@router.post(
    "/businesses/{business_id}/reports",
    response_model=ReportDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_report(
    business_id: uuid.UUID,
    body: ReportCreate,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ReportDetail:
    business = load_business(db, active.user, business_id, "edit", ip, user_agent)
    try:
        report = report_service.create_report_group(
            db, business, body.report_number.strip(), body.pipe_number.strip()
        )
    except IntegrityError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "이미 사용 중인 보고서 번호입니다."
        ) from error
    db.refresh(report)
    group = db.get(ReportGroup, report.report_group_id)
    assert group is not None
    return report_detail(db, report, group)


@router.post(
    "/report-groups/{group_id}/versions",
    response_model=ReportDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    group_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ReportDetail:
    group, project = _load_group(db, active.user, group_id, "edit", ip, user_agent)
    report = report_service.create_version_from_latest(db, group, project, store)
    db.refresh(report)
    return report_detail(db, report, group)


@router.post(
    "/report-groups/{group_id}/duplicate",
    response_model=ReportDetail,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_report(
    group_id: uuid.UUID,
    body: DuplicateTarget,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ReportDetail:
    group, _ = _load_group(db, active.user, group_id, "view", ip, user_agent)
    target: Business = load_business(
        db, active.user, body.target_business_id or group.business_id, "edit", ip, user_agent
    )
    report = report_service.duplicate_report_group(db, group, target)
    db.refresh(report)
    new_group = db.get(ReportGroup, report.report_group_id)
    assert new_group is not None
    return report_detail(db, report, new_group)


@router.delete("/report-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report_group(
    group_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> Response:
    group, project = _load_group(db, active.user, group_id, "edit", ip, user_agent)
    prefixes = [report_group_prefix(project.organization_id, project.id, group.id)] + [
        report_prefix(project.organization_id, project.id, report_id)
        for report_id in db.scalars(select(Report.id).where(Report.report_group_id == group.id))
    ]
    audit.record(
        db,
        "report.deleted",
        organization_id=project.organization_id,
        actor_user_id=active.user.id,
        target_type="report_group",
        target_id=group.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"report_number": group.report_number},
    )
    db.delete(group)
    db.flush()
    _schedule_deletes(db, store, prefixes, [])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Report versions ---------------------------------------------------------------


@router.get("/reports/{report_id}", response_model=ReportDetail)
def get_report(
    report_id: uuid.UUID, active: CurrentSession, db: DbSession, ip: ClientIp, user_agent: UserAgent
) -> ReportDetail:
    report, group, _ = load_report(db, active.user, report_id, "view", ip, user_agent)
    return report_detail(db, report, group)


@router.patch("/reports/{report_id}", response_model=ReportDetail)
def update_report(
    report_id: uuid.UUID,
    body: ReportUpdate,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ReportDetail:
    report, group, _ = load_report(db, active.user, report_id, "edit", ip, user_agent)
    changes = body.model_dump(exclude_unset=True)
    for field in ("report_number", "pipe_number"):
        if changes.get(field) is not None:
            setattr(group, field, changes.pop(field).strip())
        changes.pop(field, None)
    if changes.get("version_name") is not None:
        report.version_name = changes.pop("version_name").strip()
    changes.pop("version_name", None)
    for field, value in changes.items():
        setattr(report, field, value)
    _touch(report)
    try:
        db.flush()
    except IntegrityError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "이미 사용 중인 보고서 번호입니다."
        ) from error
    db.refresh(report)
    return report_detail(db, report, group)


@router.patch("/reports/{report_id}/pipe-information", response_model=ReportDetail)
def update_pipe_information(
    report_id: uuid.UUID,
    body: PipeUpdate,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ReportDetail:
    report, group, _ = load_report(db, active.user, report_id, "edit", ip, user_agent)
    pipe = db.scalar(select(PipeInformation).where(PipeInformation.report_id == report.id))
    assert pipe is not None
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(pipe, field, value)
    report_service.apply_pipe_derivations(pipe)
    _touch(report)
    db.flush()
    db.refresh(report)
    return report_detail(db, report, group)


@router.patch("/reports/{report_id}/manholes/{role}", response_model=ReportDetail)
def update_manhole(
    report_id: uuid.UUID,
    role: Literal["upstream", "downstream"],
    body: ManholeUpdate,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ReportDetail:
    report, group, _ = load_report(db, active.user, report_id, "edit", ip, user_agent)
    manhole = db.scalar(select(Manhole).where(Manhole.report_id == report.id, Manhole.role == role))
    assert manhole is not None
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(manhole, field, value)
    _touch(report)
    db.flush()
    db.refresh(report)
    return report_detail(db, report, group)


@router.patch("/reports/{report_id}/actual-survey", response_model=ReportDetail)
def update_actual_survey(
    report_id: uuid.UUID,
    body: SurveyUpdate,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ReportDetail:
    report, group, _ = load_report(db, active.user, report_id, "edit", ip, user_agent)
    survey = db.scalar(
        select(ActualSurveyInformation).where(ActualSurveyInformation.report_id == report.id)
    )
    assert survey is not None
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(survey, field, value)
    _touch(report)
    db.flush()
    db.refresh(report)
    return report_detail(db, report, group)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report_version(
    report_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> Response:
    report, group, project = load_report(db, active.user, report_id, "edit", ip, user_agent)
    versions = db.scalar(
        select(func.count()).select_from(Report).where(Report.report_group_id == group.id)
    )
    if versions is not None and versions <= 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "마지막 버전은 삭제할 수 없습니다. 보고서를 삭제하세요."
        )
    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report.id))
    video_keys = (
        {k for k in (video.original_object_key, video.playback_object_key) if k} if video else set()
    )
    audit.record(
        db,
        "report.version_deleted",
        organization_id=project.organization_id,
        actor_user_id=active.user.id,
        target_type="report",
        target_id=report.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"version_number": report.version_number},
    )
    db.delete(report)
    db.flush()
    _schedule_deletes(
        db,
        store,
        [report_prefix(project.organization_id, project.id, report.id)],
        _unreferenced_video_keys(db, video_keys),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
