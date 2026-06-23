from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Project:
    id: int
    project_name: str
    created_at: datetime


@dataclass
class Business:
    id: int
    project_id: int
    business_code: str
    business_name: str
    client: Optional[str]
    business_start_date: Optional[str]
    business_end_date: Optional[str]


@dataclass
class Report:
    id: int
    business_id: int
    report_number: str
    pipe_number: str
    survey_purpose: Optional[str]
    survey_date: Optional[str]
    buried_years: Optional[str]
    inspector: Optional[str]
    contractor: Optional[str]
    treatment_area: Optional[str]
    drainage_area: Optional[str]
    drainage_district: Optional[str]
    drain_type: Optional[str]
    drain_system: Optional[str]
    province: Optional[str]
    city_county: Optional[str]
    town: Optional[str]
    village: Optional[str]
    lot_number: Optional[str]
    road_address: Optional[str]
    pipe_type: Optional[str]
    category: Optional[str]
    specification: Optional[str]
    created_at: datetime


@dataclass
class Manhole:
    id: int
    report_id: int
    role: str
    manhole_number: Optional[str]
    manhole_type: Optional[str]
    internal_material: Optional[str]
    cover_material: Optional[str]
    el_m: Optional[str]
    size: Optional[str]
    depth_m: Optional[str]
    invert: Optional[str]
    ladder_shape: Optional[str]
    latitude: Optional[str]
    longitude: Optional[str]


@dataclass
class PipeInformation:
    id: int
    report_id: int
    upstream_manhole_id: Optional[int]
    downstream_manhole_id: Optional[int]
    length_m: Optional[float]
    total_drive_distance_m: Optional[float]
    is_completed: bool
    undriven_distance_m: Optional[float]


@dataclass
class ActualSurveyInformation:
    id: int
    report_id: int
    start_occurrence_point_m: Optional[float]
    start_undriven_reason: Optional[str]
    start_undriven_reason_detail: Optional[str]
    end_occurrence_point_m: Optional[float]
    end_undriven_reason: Optional[str]
    end_undriven_reason_detail: Optional[str]
    survey_content: Optional[str]


@dataclass
class Video:
    id: int
    report_id: int
    file_path: str
    duration: Optional[float]
    recorded_date: Optional[str]
    scan_direction: str


@dataclass
class Defect:
    id: int
    report_id: int
    video_id: int
    timestamp_ms: int
    image_path: str
    drive_direction: str
    distance_m: Optional[float]
    item_category: Optional[str]
    condition_item: Optional[str]
    defect_item: Optional[str]
    grade: Optional[str]
    quadrant: Optional[str]
    manhole_defect_depth_m: Optional[float]
    memo: Optional[str]
    created_at: datetime
