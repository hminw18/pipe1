from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sewerpipe_inspector.defect_taxonomy import defect_definition, defect_score


BOUNDARY_CONDITION_ITEMS = {
    "이음부(접합부)존재",
    "조사시작(순방향)",
    "조사시작(역방향)",
    "조사완료(순방향)",
    "조사완료(역방향)",
    "조사중단",
}

DIRECTION_ORDER = ("순주행", "역주행")


@dataclass(frozen=True)
class UnitStateGrade:
    index: int
    drive_direction: str
    start_label: str
    end_label: str
    start_timestamp_ms: int | None
    end_timestamp_ms: int | None
    start_distance_m: float | None
    end_distance_m: float | None
    structural_score: int
    structural_grade: int
    operational_score: int
    operational_grade: int
    defect_count: int


@dataclass(frozen=True)
class StateGradeSummary:
    sections: list[UnitStateGrade]
    structural_grade: float | None
    operational_grade: float | None


@dataclass(frozen=True)
class _GradingEvent:
    order: int
    drive_direction: str
    timestamp_ms: int | None
    distance_m: float
    condition_item: str
    is_boundary: bool
    defect_type: str | None
    score: int | None


@dataclass(frozen=True)
class _SectionPoint:
    distance_m: float
    timestamp_ms: int | None
    label: str


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        if isinstance(row, dict):
            return row.get(key, default)
        return getattr(row, key, default)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def grade_from_score(score: int | float | None) -> int:
    if score is None:
        return 1
    value = float(score)
    if value >= 70:
        return 5
    if value >= 40:
        return 4
    if value >= 20:
        return 3
    if value >= 10:
        return 2
    return 1


def _event_from_row(row: Any, order: int) -> _GradingEvent | None:
    category = str(_row_value(row, "item_category", "") or "")
    if category != "관로":
        return None

    distance_m = _to_float(_row_value(row, "distance_m"))
    if distance_m is None:
        return None

    drive_direction = str(_row_value(row, "drive_direction", "") or "")
    if drive_direction not in DIRECTION_ORDER:
        return None

    condition_item = str(_row_value(row, "condition_item", "") or "")
    defect_item = str(_row_value(row, "defect_item", "") or "")
    grade = str(_row_value(row, "grade", "") or "")

    is_boundary = condition_item in BOUNDARY_CONDITION_ITEMS and not defect_item
    score = defect_score(category, defect_item, grade) if defect_item else None
    definition = defect_definition(category, defect_item) if defect_item else None
    defect_type = definition.defect_type if definition is not None and score is not None else None

    if not is_boundary and score is None:
        return None

    return _GradingEvent(
        order=order,
        drive_direction=drive_direction,
        timestamp_ms=_to_int(_row_value(row, "timestamp_ms")),
        distance_m=distance_m,
        condition_item=condition_item,
        is_boundary=is_boundary,
        defect_type=defect_type,
        score=score,
    )


def _point_for_distance(
    events: list[_GradingEvent], distance_m: float
) -> _SectionPoint:
    boundary = next(
        (
            event
            for event in events
            if event.is_boundary and event.distance_m == distance_m
        ),
        None,
    )
    if boundary is not None:
        return _SectionPoint(
            distance_m=distance_m,
            timestamp_ms=boundary.timestamp_ms,
            label=boundary.condition_item,
        )
    event_at_distance = next(
        (event for event in events if event.distance_m == distance_m),
        None,
    )
    return _SectionPoint(
        distance_m=distance_m,
        timestamp_ms=None if event_at_distance is None else event_at_distance.timestamp_ms,
        label="",
    )


def _section_endpoints(
    events: list[_GradingEvent], reverse: bool
) -> list[tuple[_SectionPoint, _SectionPoint]]:
    boundary_distances = {event.distance_m for event in events if event.is_boundary}
    all_distances = {event.distance_m for event in events}
    if boundary_distances:
        endpoints_set = set(boundary_distances)
        min_boundary = min(boundary_distances)
        max_boundary = max(boundary_distances)
        min_distance = min(all_distances)
        max_distance = max(all_distances)
        if min_distance < min_boundary:
            endpoints_set.add(min_distance)
        if max_distance > max_boundary:
            endpoints_set.add(max_distance)
        endpoints = sorted(endpoints_set, reverse=reverse)
    else:
        endpoints = sorted(all_distances, reverse=reverse)
    if not endpoints:
        return []
    if len(endpoints) == 1:
        point = _point_for_distance(events, endpoints[0])
        return [(point, point)]
    points = [_point_for_distance(events, endpoint) for endpoint in endpoints]
    return list(zip(points, points[1:]))


def _events_in_section(
    events: list[_GradingEvent], start: float, end: float
) -> list[_GradingEvent]:
    low = min(start, end)
    high = max(start, end)
    return [
        event
        for event in events
        if not event.is_boundary and low <= event.distance_m <= high
    ]


def _score_for_type(events: list[_GradingEvent], defect_type: str) -> int:
    scores = [
        int(event.score)
        for event in events
        if event.defect_type == defect_type and event.score is not None
    ]
    return max(scores) if scores else 0


def compute_pipe_state_grades(rows: Iterable[Any]) -> StateGradeSummary:
    events = [
        event
        for index, row in enumerate(rows)
        if (event := _event_from_row(row, index)) is not None
    ]
    sections: list[UnitStateGrade] = []

    for drive_direction in DIRECTION_ORDER:
        direction_events = [
            event for event in events if event.drive_direction == drive_direction
        ]
        if not direction_events:
            continue
        reverse = drive_direction == "역주행"
        direction_events = sorted(
            direction_events,
            key=lambda event: (
                -event.distance_m if reverse else event.distance_m,
                event.order,
            ),
        )
        for start, end in _section_endpoints(direction_events, reverse):
            section_events = _events_in_section(
                direction_events, start.distance_m, end.distance_m
            )
            structural_score = _score_for_type(section_events, "구조")
            operational_score = _score_for_type(section_events, "운영")
            sections.append(
                UnitStateGrade(
                    index=len(sections) + 1,
                    drive_direction=drive_direction,
                    start_label=start.label,
                    end_label=end.label,
                    start_timestamp_ms=start.timestamp_ms,
                    end_timestamp_ms=end.timestamp_ms,
                    start_distance_m=start.distance_m,
                    end_distance_m=end.distance_m,
                    structural_score=structural_score,
                    structural_grade=grade_from_score(structural_score),
                    operational_score=operational_score,
                    operational_grade=grade_from_score(operational_score),
                    defect_count=len(section_events),
                )
            )

    if not sections:
        return StateGradeSummary([], None, None)

    structural_grade = sum(section.structural_grade for section in sections) / len(sections)
    operational_grade = sum(section.operational_grade for section in sections) / len(sections)
    return StateGradeSummary(sections, structural_grade, operational_grade)


def format_state_value(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    text = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    if "." not in text:
        text = f"{text}.0"
    return text or "0.0"


def format_state_distance(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def format_state_time(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return ""
    seconds = max(0, int(timestamp_ms) // 1000)
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def format_state_time_range(start_ms: int | None, end_ms: int | None) -> str:
    start = format_state_time(start_ms)
    end = format_state_time(end_ms)
    if start and end:
        return f"{start}~{end}"
    return start or end


def format_state_section_label(section: UnitStateGrade) -> str:
    if section.start_label and section.end_label:
        return f"{section.start_label} -> {section.end_label}"
    return section.start_label or section.end_label
