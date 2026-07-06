from sewerpipe_inspector.defect_taxonomy import (
    condition_items_for_category,
    condition_code,
    defect_code,
    display_condition_item,
    display_defect_item,
    defect_score,
    grades_for_defect,
)


def test_manhole_condition_items_use_manhole_grid_source() -> None:
    items = condition_items_for_category("맨홀")

    assert items == [
        "조사시작",
        "조사중단",
        "조사완료",
        "시야상실",
        "중심상실",
        "대상없음",
        "특이사항",
        "연결관미사용",
        "연결관존재함",
        "이음부(접합부)존재",
        "재질변경",
        "라이닝변화",
        "이상없음",
        "단위길이",
    ]
    assert "조사시작(순방향)" not in items


def test_defect_and_condition_codes_match_code_tables() -> None:
    pipe_codes = {
        "균열(원주)": "CC",
        "균열(길이)": "CL",
        "균열(복합)": "CM",
        "표면손상": "SD",
        "좌굴": "BC",
        "라이닝결함": "LD",
        "변형": "DF",
        "파손": "BK",
        "붕괴": "CX",
        "영구장애물": "PO",
        "천공": "HL",
        "연결관돌출": "LP",
        "연결관접합부": "LS",
        "이음부이탈": "JS",
        "이음부손상": "JF",
        "이음부단차": "JD",
        "역경사": "NS",
        "침하": "SG",
        "내피생성": "DE",
        "토사퇴적": "DS",
        "폐유부착": "DG",
        "임시장애물": "TO",
        "뿌리침입": "RT",
        "침입수": "IF",
        "막힘": "PB",
    }
    manhole_codes = {
        "균열(수평)": "CHm",
        "균열(수직)": "CVm",
        "표면손상(내부)": "SIm",
        "표면손상(외부)": "SOm",
        "변형": "Dm",
        "파손(내부)": "BIm",
        "파손(외부)": "BOm",
        "하수관로접속부(돌출)": "LPm",
        "하수관로접속부(접속부이상)": "LSm",
        "블록이음부(단차)": "JDm",
        "블록이음부(손상)": "JFm",
        "블록이음부(이탈)": "JSm",
        "표면단차": "SGm",
        "인버트결함": "DBm",
        "맨홀뚜껑/프레임손상": "DCm",
        "악취발생": "OAm",
        "내피생성": "DEm",
        "폐유부착": "DGm",
        "임시장애물": "TOm",
        "사다리손상": "DSm",
        "뚜껑밀폐": "SCm",
        "뿌리침입": "RIm",
        "침입수": "IFm",
    }
    condition_codes = {
        "조사시작(순방향)": "IS",
        "조사시작(역방향)": "ISr",
        "조사완료(순방향)": "IE",
        "조사완료(역방향)": "IEr",
        "조사중단": "IA",
        "시야상실": "LV",
        "중심상실": "LC",
        "대상없음": "NE",
        "특이사항": "SC",
        "연결관미사용": "LB",
        "연결관존재함": "LO",
        "이음부(접합부)존재": "JE",
        "재질변경": "MC",
        "라이닝변화": "LC",
        "이상없음": "WD",
        "단위길이": "ULm",
    }

    for item, code in pipe_codes.items():
        assert defect_code("관로", item) == code
    for item, code in manhole_codes.items():
        assert defect_code("맨홀", item) == code
    for item, code in condition_codes.items():
        assert condition_code("관로", item) == code

    assert display_defect_item("관로", "균열(길이)") == "균열(길이) (CL)"
    assert display_condition_item("관로", "조사중단") == "조사중단 (IA)"


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


def test_pipe_block_has_only_large_grade_with_100_points() -> None:
    assert grades_for_defect("관로", "막힘") == ["대"]
    assert defect_score("관로", "막힘", "대") == 100
    assert defect_score("관로", "막힘", "중") is None
    assert defect_score("관로", "막힘", "소") is None
