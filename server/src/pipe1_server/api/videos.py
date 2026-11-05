"""Report video: direct upload, processing status, playback and settings
(docs/Web_Architecture.md §4, docs/Product_Requirements.md §4.7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import PurePath
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update

from pipe1_server.api.deps import (
    AppSettings,
    ClientIp,
    CurrentSession,
    DbSession,
    Jobs,
    Now,
    Store,
    UserAgent,
)
from pipe1_server.api.reports import load_report
from pipe1_server.db import after_commit
from pipe1_server.media import ACCEPTED_EXTENSIONS
from pipe1_server.models.inspection import DefectSuggestion, MediaJob, ReportVideo, VideoUpload
from pipe1_server.security import audit
from pipe1_server.storage import (
    UPLOAD_SESSION_TTL,
    VIDEO_URL_TTL,
    LocalObjectStore,
    report_group_prefix,
)

router = APIRouter(prefix="/api", tags=["videos"])

CONTENT_TYPES = {
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
}

# Jobs whose results belong to one video; a replacement ends them and hides them.
VIDEO_ANALYSIS_KINDS = ("stop_analysis", "defect_detection")
VIDEO_REPLACED = "영상이 교체되어 중단되었습니다."


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)


class UploadSession(BaseModel):
    upload_id: uuid.UUID
    protocol: Literal["gcs-resumable", "put"]
    url: str
    headers: dict[str, str]
    expires_at: datetime


class VideoOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    size_bytes: int | None
    transcode_status: str
    processing_error: str | None
    duration_seconds: float | None
    fps: float | None
    width: int | None
    height: int | None
    recorded_date: str | None
    scan_direction: str
    depth_roi: tuple[int, int, int, int] | None
    playback_url: str | None
    playback_url_expires_in: int | None


class DepthRoi(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class VideoUpdate(BaseModel):
    scan_direction: Literal["순주행", "역주행"] | None = None
    recorded_date: str | None = Field(default=None, max_length=50)
    depth_roi: DepthRoi | None = None


def video_out(video: ReportVideo, store) -> VideoOut:
    ready = video.transcode_status == "ready"
    roi = (
        (video.depth_roi_x, video.depth_roi_y, video.depth_roi_w, video.depth_roi_h)
        if video.depth_roi_x is not None
        else None
    )
    return VideoOut(
        id=video.id,
        original_filename=video.original_filename,
        size_bytes=video.size_bytes,
        transcode_status=video.transcode_status,
        processing_error=video.processing_error,
        duration_seconds=video.duration_seconds,
        fps=video.fps,
        width=video.width,
        height=video.height,
        recorded_date=video.recorded_date,
        scan_direction=video.scan_direction,
        depth_roi=roi,
        playback_url=store.signed_download_url(video.playback_object_key, VIDEO_URL_TTL)
        if ready and video.playback_object_key
        else None,
        playback_url_expires_in=int(VIDEO_URL_TTL.total_seconds()) if ready else None,
    )


def _extension(filename: str) -> str:
    return PurePath(filename).suffix.lower()


@router.post(
    "/reports/{report_id}/video/uploads",
    response_model=UploadSession,
    status_code=status.HTTP_201_CREATED,
)
def start_upload(
    report_id: uuid.UUID,
    body: UploadRequest,
    active: CurrentSession,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> UploadSession:
    report, group, project = load_report(db, active.user, report_id, "edit", ip, user_agent)
    extension = _extension(body.filename)
    if extension not in ACCEPTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "avi, mkv, mov, mp4 영상만 올릴 수 있습니다."
        )
    if body.size_bytes > settings.max_video_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "영상 파일이 너무 큽니다.")
    used = db.execute(text("SELECT app_org_video_bytes()")).scalar_one()
    pending = db.scalar(
        select(func.coalesce(func.sum(VideoUpload.size_bytes), 0)).where(
            VideoUpload.project_id == project.id,
            VideoUpload.status == "pending",
            VideoUpload.expires_at > now,
        )
    )
    if used + pending + body.size_bytes > settings.organization_storage_quota_bytes:
        raise HTTPException(
            status.HTTP_507_INSUFFICIENT_STORAGE, "조직의 저장 공간 한도를 초과합니다."
        )
    upload_id = uuid.uuid4()
    key = (
        f"{report_group_prefix(project.organization_id, project.id, group.id)}"
        f"video/{upload_id}/original{extension}"
    )
    content_type = CONTENT_TYPES[extension]
    expires = now + UPLOAD_SESSION_TTL
    db.add(
        VideoUpload(
            id=upload_id,
            project_id=project.id,
            report_id=report.id,
            object_key=key,
            original_filename=body.filename[:255],
            content_type=content_type,
            size_bytes=body.size_bytes,
            created_by=active.user.id,
            expires_at=expires,
        )
    )
    # The first upload creates the report's video row right away ('uploading'), so defects can
    # be captured from the local file in the browser while the upload runs. A replacement
    # leaves the current video in place until the new upload completes.
    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report.id))
    if video is None:
        db.add(
            ReportVideo(
                project_id=project.id,
                report_id=report.id,
                original_object_key=key,
                original_filename=body.filename[:255],
                transcode_status="uploading",
            )
        )
    elif video.transcode_status == "uploading":
        video.original_object_key, video.original_filename = key, body.filename[:255]
    url = store.signed_upload_url(key, content_type, body.size_bytes)
    if isinstance(store, LocalObjectStore):
        return UploadSession(
            upload_id=upload_id,
            protocol="put",
            url=url,
            headers={"Content-Type": content_type},
            expires_at=expires,
        )
    return UploadSession(
        upload_id=upload_id,
        protocol="gcs-resumable",
        url=url,
        headers={
            "Content-Type": content_type,
            "x-goog-resumable": "start",
            "x-goog-content-length-range": f"0,{body.size_bytes}",
        },
        expires_at=expires,
    )


@router.post("/video-uploads/{upload_id}/complete", response_model=VideoOut)
def complete_upload(
    upload_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    now: Now,
    store: Store,
    jobs: Jobs,
    ip: ClientIp,
    user_agent: UserAgent,
) -> VideoOut:
    upload = db.get(VideoUpload, upload_id)
    if upload is None or upload.created_by != active.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    report, group, project = load_report(db, active.user, upload.report_id, "edit", ip, user_agent)
    if upload.status != "pending" or upload.expires_at <= now:
        raise HTTPException(status.HTTP_409_CONFLICT, "업로드가 만료되었습니다. 다시 올려 주세요.")
    size = store.size(upload.object_key)
    if size is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "업로드가 완료되지 않았습니다.")
    if size != upload.size_bytes:
        store.delete(upload.object_key)
        upload.status = "abandoned"
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "업로드된 파일 크기가 다릅니다.")

    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report.id))
    replaced_keys: set[str] = set()
    if video is None or video.transcode_status == "uploading":
        # First upload: fill in the row created when it started (it holds no media yet).
        if video is None:
            video = ReportVideo(project_id=project.id, report_id=report.id)
            db.add(video)
        video.original_object_key = upload.object_key
        video.original_filename = upload.original_filename
    else:
        # Keep the row (defects reference it); swap the media and reset processing.
        replaced_keys = {k for k in (video.original_object_key, video.playback_object_key) if k}
        # AI suggestions point at times in the old video.
        db.execute(delete(DefectSuggestion).where(DefectSuggestion.report_id == report.id))
        # Analyses of the old video still in progress end here, so they neither block nor
        # report on the new one.
        db.execute(
            update(MediaJob)
            .where(
                MediaJob.report_id == report.id,
                MediaJob.kind.in_(VIDEO_ANALYSIS_KINDS),
                MediaJob.status.in_(("queued", "running")),
            )
            .values(status="failed", error=VIDEO_REPLACED)
        )
        video.original_object_key = upload.object_key
        video.original_filename = upload.original_filename
        video.playback_object_key = None
        video.duration_seconds = video.fps = None
        video.width = video.height = None
        video.travel_start_s = None
    video.size_bytes = size
    video.transcode_status = "pending"
    video.processing_error = None
    upload.status = "completed"
    db.flush()
    job = MediaJob(
        project_id=project.id,
        report_id=report.id,
        kind="process_video",
        created_by=active.user.id,
    )
    db.add(job)
    db.flush()
    still_used = set(
        db.scalars(
            select(ReportVideo.original_object_key).where(
                ReportVideo.original_object_key.in_(replaced_keys)
            )
        )
    ) | set(
        db.scalars(
            select(ReportVideo.playback_object_key).where(
                ReportVideo.playback_object_key.in_(replaced_keys)
            )
        )
    )
    audit.record(
        db,
        "video.uploaded",
        organization_id=project.organization_id,
        actor_user_id=active.user.id,
        target_type="report",
        target_id=report.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"size_bytes": size, "replaced": bool(replaced_keys)},
    )
    for key in replaced_keys - still_used:
        after_commit(db, store.delete, key)
    after_commit(db, jobs.enqueue, job.id, active.user.id)
    db.refresh(video)
    return video_out(video, store)


@router.get("/reports/{report_id}/video", response_model=VideoOut)
def get_video(
    report_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> VideoOut:
    report, _, _ = load_report(db, active.user, report_id, "view", ip, user_agent)
    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report.id))
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "영상이 없습니다.")
    return video_out(video, store)


@router.patch("/reports/{report_id}/video", response_model=VideoOut)
def update_video(
    report_id: uuid.UUID,
    body: VideoUpdate,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> VideoOut:
    report, _, _ = load_report(db, active.user, report_id, "edit", ip, user_agent)
    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report.id))
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "영상이 없습니다.")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("scan_direction") is not None:
        video.scan_direction = changes["scan_direction"]
    if "recorded_date" in changes:
        video.recorded_date = changes["recorded_date"]
    if "depth_roi" in changes:
        roi = body.depth_roi
        if roi is None:
            video.depth_roi_x = video.depth_roi_y = video.depth_roi_w = video.depth_roi_h = None
        else:
            if (
                video.width
                and video.height
                and (roi.x + roi.w > video.width or roi.y + roi.h > video.height)
            ):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "깊이 영역이 영상 밖에 있습니다."
                )
            video.depth_roi_x, video.depth_roi_y = roi.x, roi.y
            video.depth_roi_w, video.depth_roi_h = roi.w, roi.h
    db.flush()
    db.refresh(video)
    return video_out(video, store)
