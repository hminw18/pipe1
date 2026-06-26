from sewerpipe_inspector.state_grading import (
    compute_pipe_state_grades,
    format_state_value,
    grade_from_score,
)


def test_grade_from_score_uses_condition_tables() -> None:
    assert grade_from_score(0) == 1
    assert grade_from_score(9) == 1
    assert grade_from_score(10) == 2
    assert grade_from_score(20) == 3
    assert grade_from_score(40) == 4
    assert grade_from_score(70) == 5
    assert grade_from_score(120) == 5


def test_unit_grades_use_max_score_by_defect_type_and_share_boundary_distance() -> None:
    rows = [
        {
            "drive_direction": "순주행",
            "distance_m": 0.0,
            "item_category": "관로",
            "condition_item": "조사시작(순방향)",
            "defect_item": None,
            "grade": None,
        },
        {
            "drive_direction": "순주행",
            "distance_m": 0.0,
            "item_category": "관로",
            "condition_item": None,
            "defect_item": "균열(길이)",
            "grade": "중",
        },
        {
            "drive_direction": "순주행",
            "distance_m": 3.0,
            "item_category": "관로",
            "condition_item": None,
            "defect_item": "침입수",
            "grade": "중",
        },
        {
            "drive_direction": "순주행",
            "distance_m": 5.0,
            "item_category": "관로",
            "condition_item": "이음부(접합부)존재",
            "defect_item": None,
            "grade": None,
        },
        {
            "drive_direction": "순주행",
            "distance_m": 5.0,
            "item_category": "관로",
            "condition_item": None,
            "defect_item": "표면손상",
            "grade": "대",
        },
        {
            "drive_direction": "순주행",
            "distance_m": 7.0,
            "item_category": "관로",
            "condition_item": None,
            "defect_item": "토사퇴적",
            "grade": "대",
        },
        {
            "drive_direction": "순주행",
            "distance_m": 10.0,
            "item_category": "관로",
            "condition_item": "조사완료(순방향)",
            "defect_item": None,
            "grade": None,
        },
    ]

    summary = compute_pipe_state_grades(rows)

    assert len(summary.sections) == 2
    assert summary.sections[0].start_distance_m == 0.0
    assert summary.sections[0].end_distance_m == 5.0
    assert summary.sections[0].structural_score == 50
    assert summary.sections[0].structural_grade == 4
    assert summary.sections[0].operational_score == 28
    assert summary.sections[0].operational_grade == 3
    assert summary.sections[1].start_distance_m == 5.0
    assert summary.sections[1].end_distance_m == 10.0
    assert summary.sections[1].structural_score == 50
    assert summary.sections[1].structural_grade == 4
    assert summary.sections[1].operational_score == 45
    assert summary.sections[1].operational_grade == 4
    assert summary.structural_grade == 4.0
    assert summary.operational_grade == 3.5
    assert format_state_value(summary.structural_grade) == "4.0"
    assert format_state_value(summary.operational_grade) == "3.5"


def test_reverse_direction_sections_are_ordered_by_descending_distance() -> None:
    rows = [
        {
            "drive_direction": "역주행",
            "distance_m": 10.0,
            "item_category": "관로",
            "condition_item": "조사시작(역방향)",
            "defect_item": None,
            "grade": None,
        },
        {
            "drive_direction": "역주행",
            "distance_m": 6.0,
            "item_category": "관로",
            "condition_item": None,
            "defect_item": "침입수",
            "grade": "대",
        },
        {
            "drive_direction": "역주행",
            "distance_m": 0.0,
            "item_category": "관로",
            "condition_item": "조사완료(역방향)",
            "defect_item": None,
            "grade": None,
        },
        {
            "drive_direction": "순주행",
            "distance_m": 0.0,
            "item_category": "맨홀",
            "condition_item": None,
            "defect_item": "침입수",
            "grade": "대",
        },
    ]

    summary = compute_pipe_state_grades(rows)

    assert len(summary.sections) == 1
    assert summary.sections[0].drive_direction == "역주행"
    assert summary.sections[0].start_distance_m == 10.0
    assert summary.sections[0].end_distance_m == 0.0
    assert summary.sections[0].operational_score == 100
    assert summary.sections[0].operational_grade == 5


def test_stop_condition_splits_unit_sections() -> None:
    rows = [
        {
            "drive_direction": "순주행",
            "timestamp_ms": 0,
            "distance_m": 0.0,
            "item_category": "관로",
            "condition_item": "조사시작(순방향)",
            "defect_item": None,
            "grade": None,
        },
        {
            "drive_direction": "순주행",
            "timestamp_ms": 5000,
            "distance_m": 5.0,
            "item_category": "관로",
            "condition_item": "조사중단",
            "defect_item": None,
            "grade": None,
        },
        {
            "drive_direction": "순주행",
            "timestamp_ms": 10000,
            "distance_m": 10.0,
            "item_category": "관로",
            "condition_item": "조사완료(순방향)",
            "defect_item": None,
            "grade": None,
        },
    ]

    summary = compute_pipe_state_grades(rows)

    assert len(summary.sections) == 2
    assert summary.sections[0].start_label == "조사시작(순방향)"
    assert summary.sections[0].end_label == "조사중단"
    assert summary.sections[1].start_label == "조사중단"
    assert summary.sections[1].end_label == "조사완료(순방향)"
