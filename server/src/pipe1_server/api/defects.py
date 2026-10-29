"""Defect capture and management (docs/Product_Requirements.md §4.8, §6.4-§6.6)."""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pipe1_core.defect_taxonomy import (
    condition_items_for_category,
    defect_definition,
    defect_score,
    grades_for_defect,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from pipe1_server.api.deps import (
    ClientIp,
    CurrentSession,
    DbSession,
    Media,
    Store,
    UserAgent,
)
from pipe1_server.api.reports import load_report
from pipe1_server.db import after_commit
from pipe1_server.ids import uuid7
from pipe1_server.media import MAX_FRAME_BYTES, MediaError
from pipe1_server.models.inspection import Report, ReportDefect, ReportVideo
from pipe1_server.services.reports import capture_key
from pipe1_server.storage import IMAGE_URL_TTL

router = APIRouter(prefix="/api", tags=["defects"])

Direction = Literal["순주행", "역주행"]
Category = Literal["맨홀", "관로", "암거"]
Grade = Literal["대", "중", "소"]
Quadrant = Literal[
    "없음",
    "상",
    "상우하",
    "우",
    "우상",
    "우상좌하",
    "우하",
    "좌",
    "좌상",
    "좌상우하",
    "좌하",
    "하",
    "하우상",
    "하좌상",
    "전체",
]


class DefectFields(BaseModel):
    drive_direction: Direction | None = None
    distance_m: float | None = None
    item_category: Category | None = None
    condition_item: str | None = Field(default=None, max_length=100)
    defect_item: str | None = Field(default=None, max_length=100)
    grade: Grade | None = None
    quadrant: Quadrant | None = None
    manhole_defect_depth_m: float | None = None
    memo: str | None = Field(default=None, max_length=2000)


# A PNG of the frame the browser showed, captured from the user's local file while it is
# still uploading or processing. Without it the server extracts the frame from the upload.
FrameField = Field(default=None, max_length=MAX_FRAME_BYTES * 4 // 3 + 4)


class DefectCreate(DefectFields):
    timestamp_ms: int = Field(ge=0)
    drive_direction: Direction
    frame_png_base64: str | None = FrameField


class DefectOut(BaseModel):
    id: uuid.UUID
    report_id: uuid.UUID
    timestamp_ms: int
    drive_direction: str
    distance_m: float
    item_category: str | None
    condition_item: str | None
    defect_item: str | None
    grade: str | None
    quadrant: str | None
    manhole_defect_depth_m: float | None
    memo: str | None
    score: int | None
    image_url: str
    created_at: datetime


class GradeSummary(BaseModel):
    large: int
    medium: int
    small: int
    total: int


class DistanceSuggestion(BaseModel):
    distance_m: float | None


class TimestampRequest(BaseModel):
    timestamp_ms: int = Field(ge=0)
    frame_png_base64: str | None = FrameField


def _validate_classification(fields: dict) -> None:
    """Desktop rules: one of condition/defect item, valid for the category; grade only for
    defects and only among the grades the taxonomy defines for that item."""
    category = fields.get("item_category")
    condition, defect, grade = (
        fields.get("condition_item"),
        fields.get("defect_item"),
        fields.get("grade"),
    )
    if condition and defect:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "상태 항목과 결함 항목은 함께 지정할 수 없습니다.",
        )
    if (condition or defect) and not category:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "항목 분류를 선택하세요.")
    if condition and condition not in condition_items_for_category(category):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "잘못된 상태 항목입니다.")
    if defect and defect_definition(category, defect) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "잘못된 결함 항목입니다.")
    if grade and not defect:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "등급은 결함에만 지정합니다.")
    if grade and grade not in grades_for_defect(category, defect):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "이 결함에 없는 등급입니다.")


def defect_out(defect: ReportDefect, store) -> DefectOut:
    score = (
        defect_score(defect.item_category, defect.defect_item, defect.grade)
        if defect.item_category and defect.defect_item and defect.grade
        else None
    )
    return DefectOut(
        id=defect.id,
        report_id=defect.report_id,
        timestamp_ms=defect.timestamp_ms,
        drive_direction=defect.drive_direction,
        distance_m=defect.distance_m,
        item_category=defect.item_category,
        condition_item=defect.condition_item,
        defect_item=defect.defect_item,
        grade=defect.grade,
        quadrant=defect.quadrant,
        manhole_defect_depth_m=defect.manhole_defect_depth_m,
        memo=defect.memo,
        score=score,
        image_url=store.signed_download_url(defect.image_object_key, IMAGE_URL_TTL),
        created_at=defect.created_at,
    )


def _video_for_capture(db, report_id: uuid.UUID, browser_frame: bool) -> ReportVideo:
    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report_id))
    if video is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 영상을 등록하세요.")
    # A browser frame needs only the video row (created when the upload starts).
    if not browser_frame and video.transcode_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "영상 처리가 끝난 뒤 캡처할 수 있습니다.")
    return video


def _frame(
    media, video: ReportVideo, timestamp_ms: int, frame_png_base64: str | None
) -> tuple[bytes, float | None]:
    if frame_png_base64 is None:
        return media.capture(video.original_object_key, timestamp_ms, _roi(video))
    try:
        return media.from_image(base64.b64decode(frame_png_base64, validate=True), _roi(video))
    except (binascii.Error, MediaError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "캡처한 화면을 읽을 수 없습니다."
        ) from error


def _roi(video: ReportVideo) -> tuple[int, int, int, int] | None:
    if video.depth_roi_x is None:
        return None
    return (video.depth_roi_x, video.depth_roi_y, video.depth_roi_w, video.depth_roi_h)


def _touch(db, report_id: uuid.UUID) -> None:
    report = db.get(Report, report_id)
    if report is not None:
        report.updated_at = func.now()


@router.get("/reports/{report_id}/defects", response_model=list[DefectOut])
def list_defects(
    report_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> list[DefectOut]:
    load_report(db, active.user, report_id, "view", ip, user_agent)
    defects = db.scalars(
        select(ReportDefect)
        .where(ReportDefect.report_id == report_id)
        .order_by(ReportDefect.timestamp_ms, ReportDefect.id)
    )
    return [defect_out(d, store) for d in defects]


@router.post("/reports/{report_id}/distance", response_model=DistanceSuggestion)
def suggest_distance(
    report_id: uuid.UUID,
    body: TimestampRequest,
    active: CurrentSession,
    db: DbSession,
    media: Media,
    ip: ClientIp,
    user_agent: UserAgent,
) -> DistanceSuggestion:
    """Depth-counter OCR at a timestamp, to prefill the distance field."""
    load_report(db, active.user, report_id, "view", ip, user_agent)
    video = _video_for_capture(db, report_id, body.frame_png_base64 is not None)
    if _roi(video) is None:
        return DistanceSuggestion(distance_m=None)
    _, distance = _frame(media, video, body.timestamp_ms, body.frame_png_base64)
    return DistanceSuggestion(distance_m=distance)


@router.post(
    "/reports/{report_id}/defects", response_model=DefectOut, status_code=status.HTTP_201_CREATED
)
def capture_defect(
    report_id: uuid.UUID,
    body: DefectCreate,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    media: Media,
    ip: ClientIp,
    user_agent: UserAgent,
) -> DefectOut:
    """Save the defect with the full-resolution frame at timestamp_ms: extracted server-side,
    or the frame the browser captured from the local file."""
    report, _, project = load_report(db, active.user, report_id, "edit", ip, user_agent)
    fields = body.model_dump(exclude={"frame_png_base64"})
    _validate_classification(fields)
    video = _video_for_capture(db, report.id, body.frame_png_base64 is not None)
    if video.duration_seconds and body.timestamp_ms > video.duration_seconds * 1000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "영상 길이를 벗어난 시점입니다.")
    png, ocr_distance = _frame(media, video, body.timestamp_ms, body.frame_png_base64)
    distance = body.distance_m if body.distance_m is not None else ocr_distance
    # Validate before storing anything, so a rejected capture leaves no orphan image.
    if distance is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "code": "distance_required",
                "message": "거리를 인식하지 못했습니다. 직접 입력하세요.",
            },
        )
    defect_id = uuid7()
    key = capture_key(project, report.id, defect_id)
    store.put_bytes(key, png, "image/png")
    defect = ReportDefect(
        id=defect_id,
        project_id=project.id,
        report_id=report.id,
        video_id=video.id,
        timestamp_ms=body.timestamp_ms,
        image_object_key=key,
        drive_direction=body.drive_direction,
        distance_m=distance,
        item_category=body.item_category,
        condition_item=body.condition_item,
        defect_item=body.defect_item,
        grade=body.grade,
        quadrant=body.quadrant,
        manhole_defect_depth_m=body.manhole_defect_depth_m,
        memo=body.memo,
        created_by=active.user.id,
    )
    db.add(defect)
    _touch(db, report.id)
    try:
        db.flush()
    except Exception:
        store.delete(key)
        raise
    db.refresh(defect)
    return defect_out(defect, store)


def _load_defect(db, user, defect_id: uuid.UUID, action, ip, user_agent) -> ReportDefect:
    defect = db.get(ReportDefect, defect_id)
    if defect is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    load_report(db, user, defect.report_id, action, ip, user_agent)
    return defect


@router.patch("/defects/{defect_id}", response_model=DefectOut)
def update_defect(
    defect_id: uuid.UUID,
    body: DefectFields,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> DefectOut:
    defect = _load_defect(db, active.user, defect_id, "edit", ip, user_agent)
    changes = body.model_dump(exclude_unset=True)
    if "distance_m" in changes and changes["distance_m"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "거리는 비울 수 없습니다.")
    if "drive_direction" in changes and changes["drive_direction"] is None:
        changes.pop("drive_direction")
    merged = {
        field: changes.get(field, getattr(defect, field))
        for field in ("item_category", "condition_item", "defect_item", "grade")
    }
    _validate_classification(merged)
    for field, value in changes.items():
        setattr(defect, field, value)
    _touch(db, defect.report_id)
    db.flush()
    db.refresh(defect)
    return defect_out(defect, store)


@router.delete("/defects/{defect_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_defect(
    defect_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> Response:
    defect = _load_defect(db, active.user, defect_id, "edit", ip, user_agent)
    key, report_id = defect.image_object_key, defect.report_id
    db.delete(defect)
    _touch(db, report_id)
    # Each defect owns its image (unique key), so deleting it never affects another defect.
    after_commit(db, store.delete, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/reports/{report_id}/grade-summary", response_model=GradeSummary)
def grade_summary(
    report_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> GradeSummary:
    """Computed on demand, never cached (Product_Requirements §6.6)."""
    load_report(db, active.user, report_id, "view", ip, user_agent)
    counts = dict(
        db.execute(
            select(ReportDefect.grade, func.count())
            .where(ReportDefect.report_id == report_id, ReportDefect.grade.is_not(None))
            .group_by(ReportDefect.grade)
        ).all()
    )
    large, medium, small = counts.get("대", 0), counts.get("중", 0), counts.get("소", 0)
    return GradeSummary(large=large, medium=medium, small=small, total=large + medium + small)
