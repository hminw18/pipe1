"""Stop analysis and export jobs, and their status (docs/Product_Requirements.md §6.7-§6.8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from pipe1_server.api.deps import (
    ClientIp,
    CurrentSession,
    DbSession,
    Jobs,
    Store,
    UserAgent,
)
from pipe1_server.api.projects import load_business
from pipe1_server.api.reports import load_report
from pipe1_server.api.videos import VIDEO_ANALYSIS_KINDS
from pipe1_server.db import after_commit
from pipe1_server.models.inspection import MediaJob, Report, ReportGroup, ReportVideo
from pipe1_server.security import audit
from pipe1_server.services import exports
from pipe1_server.services.reports import export_readiness, latest_version
from pipe1_server.storage import IMAGE_URL_TTL

router = APIRouter(prefix="/api", tags=["jobs"])


class ExportRequest(BaseModel):
    kind: Literal["excel", "pdf"]
    report_type: Literal["inspection", "post_repair", "comparison"] = "inspection"
    after_report_id: uuid.UUID | None = None


class BusinessExportRequest(BaseModel):
    workbook: Literal["internal", "aggregate"]
    report_group_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    include_photos: bool = False


class JobOut(BaseModel):
    id: uuid.UUID
    report_id: uuid.UUID
    kind: str
    status: str
    progress: int
    message: str | None
    error: str | None
    result: dict[str, Any] | None
    download_url: str | None
    created_at: datetime
    updated_at: datetime


def job_out(job: MediaJob, store) -> JobOut:
    download_url = None
    if job.status == "succeeded" and job.result and job.result.get("object_key"):
        download_url = store.signed_download_url(
            job.result["object_key"],
            IMAGE_URL_TTL,
            filename=job.result.get("filename"),
            attachment=True,
        )
    result = job.result
    if result and "object_key" in result:
        result = {k: v for k, v in result.items() if k != "object_key"}
    return JobOut(
        id=job.id,
        report_id=job.report_id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        message=job.message,
        error=job.error,
        result=result,
        download_url=download_url,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def create_job(db, jobs, *, report: Report, kind: str, user_id, parameters) -> MediaJob:
    job = MediaJob(
        project_id=report.project_id,
        report_id=report.id,
        kind=kind,
        parameters=parameters,
        created_by=user_id,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    after_commit(db, jobs.enqueue, job.id, user_id)
    return job


@router.post(
    "/reports/{report_id}/stop-analysis",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_stop_analysis(
    report_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    jobs: Jobs,
    ip: ClientIp,
    user_agent: UserAgent,
) -> JobOut:
    report, _, _ = load_report(db, active.user, report_id, "edit", ip, user_agent)
    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report.id))
    if video is None or video.transcode_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "영상 처리가 끝난 뒤 분석할 수 있습니다.")
    if video.depth_roi_x is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 깊이 영역을 저장하세요.")
    running = db.scalar(
        select(MediaJob.id).where(
            MediaJob.report_id == report.id,
            MediaJob.kind == "stop_analysis",
            MediaJob.status.in_(("queued", "running")),
        )
    )
    if running is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 의심구간 분석이 진행 중입니다.")
    job = create_job(
        db,
        jobs,
        report=report,
        kind="stop_analysis",
        user_id=active.user.id,
        parameters={},
    )
    return job_out(job, store)


@router.post(
    "/reports/{report_id}/exports", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED
)
def start_export(
    report_id: uuid.UUID,
    body: ExportRequest,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    jobs: Jobs,
    ip: ClientIp,
    user_agent: UserAgent,
) -> JobOut:
    report, group, project = load_report(db, active.user, report_id, "view", ip, user_agent)
    readiness = export_readiness(db, report, group)
    if not readiness.ready:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "export_fields_missing", "missing": readiness.missing},
        )
    parameters: dict[str, Any] = {}
    if body.kind == "pdf":
        parameters["report_type"] = body.report_type
        if body.report_type == "comparison":
            if body.after_report_id is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "비교할 보수후 보고서를 선택하세요."
                )
            load_report(db, active.user, body.after_report_id, "view", ip, user_agent)
            differences = exports.comparison_mismatches(
                exports.load_report_data(db, report.id),
                exports.load_report_data(db, body.after_report_id),
            )
            if differences:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {"code": "comparison_mismatch", "fields": differences},
                )
            parameters["after_report_id"] = str(body.after_report_id)
    job = create_job(
        db,
        jobs,
        report=report,
        kind="export_excel" if body.kind == "excel" else "export_pdf",
        user_id=active.user.id,
        parameters=parameters,
    )
    audit.record(
        db,
        "report.exported",
        organization_id=project.organization_id,
        actor_user_id=active.user.id,
        target_type="report",
        target_id=report.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"kind": body.kind, **parameters},
    )
    return job_out(job, store)


@router.post(
    "/businesses/{business_id}/exports",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_business_export(
    business_id: uuid.UUID,
    body: BusinessExportRequest,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    jobs: Jobs,
    ip: ClientIp,
    user_agent: UserAgent,
) -> JobOut:
    business = load_business(db, active.user, business_id, "view", ip, user_agent)
    reports: list[Report] = []
    for group_id in body.report_group_ids:
        group = db.get(ReportGroup, group_id)
        if group is None or group.business_id != business.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        latest = latest_version(db, group.id)
        if latest is not None:
            reports.append(latest)
    if not reports:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "선택된 보고서가 없습니다.")
    job = create_job(
        db,
        jobs,
        report=reports[0],
        kind="export_business",
        user_id=active.user.id,
        parameters={
            "workbook": body.workbook,
            "report_ids": [str(r.id) for r in reports],
            "include_photos": body.include_photos,
        },
    )
    audit.record(
        db,
        "business.exported",
        organization_id=active.user.organization_id,
        actor_user_id=active.user.id,
        target_type="business",
        target_id=business.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"workbook": body.workbook, "reports": len(reports)},
    )
    return job_out(job, store)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> JobOut:
    job = db.get(MediaJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    load_report(db, active.user, job.report_id, "view", ip, user_agent)
    return job_out(job, store)


@router.get("/reports/{report_id}/jobs", response_model=list[JobOut])
def list_report_jobs(
    report_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
    kind: str | None = None,
) -> list[JobOut]:
    load_report(db, active.user, report_id, "view", ip, user_agent)
    query = select(MediaJob).where(MediaJob.report_id == report_id)
    if kind:
        query = query.where(MediaJob.kind == kind)
    if kind in VIDEO_ANALYSIS_KINDS:
        # Only analyses of the current video: those started after its upload was processed.
        uploaded = db.scalar(
            select(func.max(MediaJob.created_at)).where(
                MediaJob.report_id == report_id, MediaJob.kind == "process_video"
            )
        )
        if uploaded is not None:
            query = query.where(MediaJob.created_at > uploaded)
    jobs = db.scalars(query.order_by(MediaJob.created_at.desc()).limit(20))
    return [job_out(job, store) for job in jobs]
