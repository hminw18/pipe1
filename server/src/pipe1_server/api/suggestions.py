"""AI defect suggestions: start detection, list suggestions, reject or restore them.

A suggestion is confirmed by recording a defect from it (POST /reports/{id}/defects with
suggestion_id), never by itself (pipe1_server.detection).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from pipe1_server.api.deps import (
    AppSettings,
    ClientIp,
    CurrentSession,
    DbSession,
    Jobs,
    Store,
    UserAgent,
)
from pipe1_server.api.jobs import JobOut, create_job, job_out
from pipe1_server.api.reports import load_report
from pipe1_server.detection import build_detector
from pipe1_server.jobs import DETECTION_OFF
from pipe1_server.models.inspection import DefectSuggestion, MediaJob, ReportVideo

router = APIRouter(prefix="/api", tags=["suggestions"])


class BoxSampleOut(BaseModel):
    t_ms: int
    box: list[float]
    score: float | None


class SuggestionOut(BaseModel):
    id: uuid.UUID
    code: str
    item_category: str
    defect_item: str
    confidence: float
    start_ms: int
    end_ms: int
    best_ms: int
    best_box: list[float] | None
    distance_m: float | None
    boxes: list[BoxSampleOut]
    status: str
    defect_id: uuid.UUID | None
    model_version: str


class SuggestionList(BaseModel):
    # False when no detector is configured: the UI hides the feature.
    available: bool
    suggestions: list[SuggestionOut]
    # When the camera set off into the pipe (from stop analysis; None until it has run), and
    # how many suggestions from before it are left out: the surface and the manhole.
    travel_start_ms: int | None = None
    before_travel: int = 0


class SuggestionUpdate(BaseModel):
    status: Literal["pending", "rejected"]


def suggestion_out(suggestion: DefectSuggestion) -> SuggestionOut:
    boxes: list[dict[str, Any]] = suggestion.boxes or []
    return SuggestionOut(
        id=suggestion.id,
        code=suggestion.code,
        item_category=suggestion.item_category,
        defect_item=suggestion.defect_item,
        confidence=suggestion.confidence,
        start_ms=suggestion.start_ms,
        end_ms=suggestion.end_ms,
        best_ms=suggestion.best_ms,
        best_box=suggestion.best_box,
        distance_m=suggestion.distance_m,
        boxes=[BoxSampleOut.model_validate(sample) for sample in boxes],
        status=suggestion.status,
        defect_id=suggestion.defect_id,
        model_version=suggestion.model_version,
    )


@router.post(
    "/reports/{report_id}/defect-detection",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_defect_detection(
    report_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    jobs: Jobs,
    settings: AppSettings,
    ip: ClientIp,
    user_agent: UserAgent,
) -> JobOut:
    report, _, _ = load_report(db, active.user, report_id, "edit", ip, user_agent)
    if build_detector(settings) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, DETECTION_OFF)
    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report.id))
    if video is None or video.transcode_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "영상 처리가 끝난 뒤 분석할 수 있습니다.")
    running = db.scalar(
        select(MediaJob.id).where(
            MediaJob.report_id == report.id,
            MediaJob.kind == "defect_detection",
            MediaJob.status.in_(("queued", "running")),
        )
    )
    if running is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 AI 분석이 진행 중입니다.")
    job = create_job(
        db, jobs, report=report, kind="defect_detection", user_id=active.user.id, parameters={}
    )
    return job_out(job, store)


@router.get("/reports/{report_id}/suggestions", response_model=SuggestionList)
def list_suggestions(
    report_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    settings: AppSettings,
    ip: ClientIp,
    user_agent: UserAgent,
) -> SuggestionList:
    load_report(db, active.user, report_id, "view", ip, user_agent)
    suggestions = list(
        db.scalars(
            select(DefectSuggestion)
            .where(DefectSuggestion.report_id == report_id)
            .order_by(DefectSuggestion.start_ms, DefectSuggestion.id)
        )
    )
    travel_start = db.scalar(
        select(ReportVideo.travel_start_s).where(ReportVideo.report_id == report_id)
    )
    travel_start_ms = round(travel_start * 1000) if travel_start is not None else None
    # Judged by the frame that shows it best; one already recorded as a defect always stays.
    shown = [
        s
        for s in suggestions
        if travel_start_ms is None or s.best_ms >= travel_start_ms or s.status == "confirmed"
    ]
    return SuggestionList(
        available=build_detector(settings) is not None,
        suggestions=[suggestion_out(s) for s in shown],
        travel_start_ms=travel_start_ms,
        before_travel=len(suggestions) - len(shown),
    )


@router.patch("/suggestions/{suggestion_id}", response_model=SuggestionOut)
def update_suggestion(
    suggestion_id: uuid.UUID,
    body: SuggestionUpdate,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> SuggestionOut:
    suggestion = db.get(DefectSuggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    load_report(db, active.user, suggestion.report_id, "edit", ip, user_agent)
    if suggestion.status == "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 결함으로 등록된 제안입니다.")
    suggestion.status = body.status
    db.flush()
    return suggestion_out(suggestion)
