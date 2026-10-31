"""Inspection data: Project → Business → Report, ported from the desktop SQLite schema.

Every table below inspection_projects carries project_id. Composite foreign keys such as
(business_id, project_id) → businesses(id, project_id) guarantee it matches the parent's
project, so row-level security can decide access with a single project lookup
(see migrations/versions/0001_initial_schema.py).

Report versions: report_groups holds the identity shared by all versions of one report
(report_number, pipe_number); reports holds one row per version.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pipe1_server.models.base import Base, CreatedAt, Timestamps, UuidPrimaryKey

DRIVE_DIRECTIONS = ("순주행", "역주행")
ITEM_CATEGORIES = ("맨홀", "관로", "암거")
GRADES = ("대", "중", "소")


def _belongs_to(parent: str, column: str) -> ForeignKeyConstraint:
    """(column, project_id) → parent(id, project_id), cascading deletes."""
    return ForeignKeyConstraint(
        [column, "project_id"], [f"{parent}.id", f"{parent}.project_id"], ondelete="CASCADE"
    )


class InspectionProject(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "inspection_projects"
    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'org')", name="visibility"),
        # The owner must be a member of the project's organization.
        ForeignKeyConstraint(
            ["owner_user_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    visibility: Mapped[str] = mapped_column(String(16))
    project_name: Mapped[str] = mapped_column(Text)


class Business(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "businesses"
    __table_args__ = (
        UniqueConstraint("project_id", "business_code"),
        UniqueConstraint("id", "project_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inspection_projects.id", ondelete="CASCADE")
    )
    business_code: Mapped[str] = mapped_column(Text)
    business_name: Mapped[str] = mapped_column(Text)
    client: Mapped[str | None] = mapped_column(Text)
    business_start_date: Mapped[str | None] = mapped_column(Text)
    business_end_date: Mapped[str | None] = mapped_column(Text)


class ReportGroup(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "report_groups"
    __table_args__ = (
        _belongs_to("businesses", "business_id"),
        UniqueConstraint("business_id", "report_number"),
        UniqueConstraint("id", "project_id"),
    )

    project_id: Mapped[uuid.UUID]
    business_id: Mapped[uuid.UUID]
    report_number: Mapped[str] = mapped_column(Text)
    pipe_number: Mapped[str] = mapped_column(Text)


class Report(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "reports"
    __table_args__ = (
        _belongs_to("report_groups", "report_group_id"),
        UniqueConstraint("report_group_id", "version_number"),
        UniqueConstraint("id", "project_id"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
    )

    project_id: Mapped[uuid.UUID]
    report_group_id: Mapped[uuid.UUID]
    version_number: Mapped[int]
    version_name: Mapped[str] = mapped_column(Text)

    survey_purpose: Mapped[str | None] = mapped_column(Text)
    survey_date: Mapped[str | None] = mapped_column(Text)
    buried_years: Mapped[str | None] = mapped_column(Text)
    inspector: Mapped[str | None] = mapped_column(Text)
    contractor: Mapped[str | None] = mapped_column(Text)
    treatment_area: Mapped[str | None] = mapped_column(Text)
    drainage_area: Mapped[str | None] = mapped_column(Text)
    drainage_district: Mapped[str | None] = mapped_column(Text)
    drain_type: Mapped[str | None] = mapped_column(Text)
    drain_system: Mapped[str | None] = mapped_column(Text)
    province: Mapped[str | None] = mapped_column(Text)
    city_county: Mapped[str | None] = mapped_column(Text)
    town: Mapped[str | None] = mapped_column(Text)
    village: Mapped[str | None] = mapped_column(Text)
    lot_number: Mapped[str | None] = mapped_column(Text)
    road_address: Mapped[str | None] = mapped_column(Text)
    pipe_type: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    specification: Mapped[str | None] = mapped_column(Text)


class Manhole(UuidPrimaryKey, Base):
    __tablename__ = "manholes"
    __table_args__ = (
        _belongs_to("reports", "report_id"),
        UniqueConstraint("report_id", "role"),
        CheckConstraint("role IN ('upstream', 'downstream')", name="role"),
    )

    project_id: Mapped[uuid.UUID]
    report_id: Mapped[uuid.UUID]
    role: Mapped[str] = mapped_column(String(16))
    # Free text on the desktop, including the numeric-looking fields; kept as text.
    manhole_number: Mapped[str | None] = mapped_column(Text)
    manhole_type: Mapped[str | None] = mapped_column(Text)
    internal_material: Mapped[str | None] = mapped_column(Text)
    cover_material: Mapped[str | None] = mapped_column(Text)
    el_m: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    depth_m: Mapped[str | None] = mapped_column(Text)
    invert: Mapped[str | None] = mapped_column(Text)
    ladder_shape: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[str | None] = mapped_column(Text)
    longitude: Mapped[str | None] = mapped_column(Text)


class PipeInformation(UuidPrimaryKey, Base):
    __tablename__ = "pipe_information"
    __table_args__ = (_belongs_to("reports", "report_id"),)

    project_id: Mapped[uuid.UUID]
    report_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    upstream_manhole_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("manholes.id", ondelete="SET NULL")
    )
    downstream_manhole_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("manholes.id", ondelete="SET NULL")
    )
    length_m: Mapped[float | None] = mapped_column(Float)
    total_drive_distance_m: Mapped[float | None] = mapped_column(Float)
    is_completed: Mapped[bool] = mapped_column(server_default=text("false"))
    undriven_distance_m: Mapped[float | None] = mapped_column(Float)


class ActualSurveyInformation(UuidPrimaryKey, Base):
    __tablename__ = "actual_survey_information"
    __table_args__ = (_belongs_to("reports", "report_id"),)

    project_id: Mapped[uuid.UUID]
    report_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    start_occurrence_point_m: Mapped[float | None] = mapped_column(Float)
    start_undriven_reason: Mapped[str | None] = mapped_column(Text, server_default="없음")
    start_undriven_reason_detail: Mapped[str | None] = mapped_column(Text)
    end_occurrence_point_m: Mapped[float | None] = mapped_column(Float)
    end_undriven_reason: Mapped[str | None] = mapped_column(Text, server_default="없음")
    end_undriven_reason_detail: Mapped[str | None] = mapped_column(Text)
    survey_content: Mapped[str | None] = mapped_column(Text)


class ReportVideo(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "report_videos"
    __table_args__ = (
        _belongs_to("reports", "report_id"),
        UniqueConstraint("id", "report_id"),
        CheckConstraint(f"scan_direction IN {DRIVE_DIRECTIONS}", name="scan_direction"),
        CheckConstraint(
            "transcode_status IN ('uploading', 'pending', 'processing', 'ready', 'failed')",
            name="transcode_status",
        ),
        CheckConstraint(
            "(depth_roi_x IS NULL) = (depth_roi_y IS NULL)"
            " AND (depth_roi_x IS NULL) = (depth_roi_w IS NULL)"
            " AND (depth_roi_x IS NULL) = (depth_roi_h IS NULL)",
            name="depth_roi_complete",
        ),
        CheckConstraint(
            "depth_roi_x >= 0 AND depth_roi_y >= 0 AND depth_roi_w > 0 AND depth_roi_h > 0",
            name="depth_roi_bounds",
        ),
    )

    project_id: Mapped[uuid.UUID]
    report_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    # Versions of a report share one uploaded video (objects live under the report group's
    # prefix), so these keys are not unique; objects are deleted when no row references them.
    original_object_key: Mapped[str] = mapped_column(Text, index=True)
    original_filename: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    playback_object_key: Mapped[str | None] = mapped_column(Text)
    transcode_status: Mapped[str] = mapped_column(String(16), server_default="pending")
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    fps: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None]
    height: Mapped[int | None]
    recorded_date: Mapped[str | None] = mapped_column(Text)
    scan_direction: Mapped[str] = mapped_column(String(16), server_default="순주행")
    depth_roi_x: Mapped[int | None]
    depth_roi_y: Mapped[int | None]
    depth_roi_w: Mapped[int | None]
    depth_roi_h: Mapped[int | None]
    processing_error: Mapped[str | None] = mapped_column(Text)


class ReportDefect(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "report_defects"
    __table_args__ = (
        _belongs_to("reports", "report_id"),
        # The capturing video must belong to the same report.
        ForeignKeyConstraint(
            ["video_id", "report_id"], ["report_videos.id", "report_videos.report_id"]
        ),
        CheckConstraint("timestamp_ms >= 0", name="timestamp_non_negative"),
        CheckConstraint(f"drive_direction IN {DRIVE_DIRECTIONS}", name="drive_direction"),
        CheckConstraint(f"item_category IN {ITEM_CATEGORIES}", name="item_category"),
        CheckConstraint(f"grade IN {GRADES}", name="grade"),
        # Desktop rule: a row records either a defect or a condition, and only
        # defects carry a grade.
        CheckConstraint(
            "NOT (defect_item IS NOT NULL AND condition_item IS NOT NULL)",
            name="defect_or_condition",
        ),
        CheckConstraint("grade IS NULL OR defect_item IS NOT NULL", name="grade_needs_defect"),
        Index("ix_report_defects_report_time", "report_id", "timestamp_ms"),
    )

    project_id: Mapped[uuid.UUID]
    report_id: Mapped[uuid.UUID]
    video_id: Mapped[uuid.UUID]
    timestamp_ms: Mapped[int] = mapped_column(BigInteger)
    # One object per defect: captures from the same frame never share an image.
    image_object_key: Mapped[str] = mapped_column(Text, unique=True)
    drive_direction: Mapped[str] = mapped_column(String(16))
    distance_m: Mapped[float] = mapped_column(Float)
    item_category: Mapped[str | None] = mapped_column(String(16))
    condition_item: Mapped[str | None] = mapped_column(Text)
    defect_item: Mapped[str | None] = mapped_column(Text)
    grade: Mapped[str | None] = mapped_column(String(4))
    quadrant: Mapped[str | None] = mapped_column(Text)
    manhole_defect_depth_m: Mapped[float | None] = mapped_column(Float)
    memo: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class DefectSuggestion(UuidPrimaryKey, CreatedAt, Base):
    """A defect track found by the AI detector (pipe1_server.detection). An inspector confirms
    it by recording a defect from it, or rejects it; it never becomes a defect by itself."""

    __tablename__ = "defect_suggestions"
    __table_args__ = (
        _belongs_to("reports", "report_id"),
        ForeignKeyConstraint(
            ["video_id", "report_id"],
            ["report_videos.id", "report_videos.report_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="status"),
        CheckConstraint(f"item_category IN {ITEM_CATEGORIES}", name="item_category"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint(
            "0 <= start_ms AND start_ms <= best_ms AND best_ms <= end_ms", name="time_order"
        ),
        Index("ix_defect_suggestions_report_time", "report_id", "start_ms"),
    )

    project_id: Mapped[uuid.UUID]
    report_id: Mapped[uuid.UUID]
    video_id: Mapped[uuid.UUID]
    model_version: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(String(16))
    item_category: Mapped[str] = mapped_column(String(16))
    defect_item: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    best_ms: Mapped[int] = mapped_column(BigInteger)
    # [x1, y1, x2, y2] normalized to the frame; absent when the model reports time spans only.
    best_box: Mapped[list[float] | None] = mapped_column(JSONB)
    # [{"t_ms": int, "box": [x1, y1, x2, y2], "score": float | None}, ...] in time order.
    boxes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    # The defect recorded from this suggestion.
    defect_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_defects.id", ondelete="SET NULL")
    )


class VideoUpload(UuidPrimaryKey, CreatedAt, Base):
    """A direct-to-storage upload in progress. The report's video is only replaced when the
    upload completes, so an abandoned upload never disturbs existing work."""

    __tablename__ = "video_uploads"
    __table_args__ = (
        _belongs_to("reports", "report_id"),
        CheckConstraint("status IN ('pending', 'completed', 'abandoned')", name="status"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
    )

    project_id: Mapped[uuid.UUID]
    report_id: Mapped[uuid.UUID] = mapped_column(index=True)
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    original_filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MediaJob(UuidPrimaryKey, Timestamps, Base):
    """Background work for a report: video processing, stop analysis, exports."""

    __tablename__ = "media_jobs"
    __table_args__ = (
        _belongs_to("reports", "report_id"),
        CheckConstraint(
            "kind IN ('process_video', 'stop_analysis', 'defect_detection', 'export_excel',"
            " 'export_pdf', 'export_business')",
            name="kind",
        ),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="status"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_range"),
        Index("ix_media_jobs_report_kind", "report_id", "kind", "created_at"),
    )

    project_id: Mapped[uuid.UUID]
    report_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    progress: Mapped[int] = mapped_column(server_default=text("0"))
    message: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
