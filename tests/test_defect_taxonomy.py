from sewerpipe_inspector.defect_taxonomy import (
    condition_items_for_category,
    defect_score,
    grades_for_defect,
)


def test_manhole_condition_items_use_manhole_grid_source() -> None:
    items = condition_items_for_category("맨홀")

    assert items == [
        "구조특징",
        "조사시작",
        "조사중단",
        "조사완료",
        "연결관미사용",
        "라이닝변화",
        "하수관로접속부존재",
        "블록이음부존재",
        "대상없음",
        "보수 후",
        "기타",
        "이상없음",
    ]
    assert "조사시작(순방향)" not in items


def test_pipe_damage_grade_scores_exclude_empty_medium_grade() -> None:
    assert grades_for_defect("관로", "파손") == ["대", "소"]
    assert defect_score("관로", "파손", "대") == 90
    assert defect_score("관로", "파손", "중") is None
    assert defect_score("관로", "파손", "소") == 70


def test_single_grade_defect_has_only_that_grade() -> None:
    assert grades_for_defect("관로", "붕괴") == ["대"]
    assert defect_score("관로", "붕괴", "대") == 100
    assert grades_for_defect("맨홀", "악취발생") == ["소"]
    assert defect_score("맨홀", "악취발생", "소") == 20
