from __future__ import annotations

from dataclasses import dataclass


GRADE_ORDER = ["대", "중", "소"]
ITEM_CATEGORIES = ["맨홀", "관로", "암거"]

PIPE_CONDITION_ITEMS = [
    "조사시작(순방향)",
    "조사시작(역방향)",
    "조사완료(순방향)",
    "조사완료(역방향)",
    "조사중단",
    "구조특징",
    "치수변화",
    "연결관미사용",
    "라이닝변화",
    "연결관존재함",
    "관종변경",
    "이음부(접합부)존재",
    "보수 후",
    "기타",
    "이상없음",
]

CONDITION_ITEMS_BY_CATEGORY = {
    "맨홀": [
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
    ],
    "관로": PIPE_CONDITION_ITEMS,
    "암거": PIPE_CONDITION_ITEMS,
}


@dataclass(frozen=True)
class DefectItemDefinition:
    item: str
    defect_type: str
    scores: dict[str, int]


DEFECT_ITEMS_BY_CATEGORY = {
    "맨홀": [
        DefectItemDefinition("파손(내부)", "구조", {"대": 80}),
        DefectItemDefinition("균열(수직)", "구조", {"대": 45, "중": 14, "소": 5}),
        DefectItemDefinition("균열(수평)", "구조", {"대": 40, "중": 13, "소": 4}),
        DefectItemDefinition("표면손상(내부)", "구조", {"대": 55, "중": 30, "소": 10}),
        DefectItemDefinition("인버트결함", "구조", {"대": 20, "소": 15}),
        DefectItemDefinition("맨홀뚜껑/프레임손상", "구조", {"대": 60, "중": 40, "소": 1}),
        DefectItemDefinition("하수관로접속부(돌출)", "구조", {"대": 25, "중": 10, "소": 3}),
        DefectItemDefinition("하수관로접속부(접속부이상)", "구조", {"대": 30, "중": 10, "소": 3}),
        DefectItemDefinition("변형", "구조", {"대": 70, "중": 45, "소": 25}),
        DefectItemDefinition("블록이음부(단차)", "구조", {"대": 58, "중": 40, "소": 20}),
        DefectItemDefinition("표면손상(외부)", "구조", {"대": 50, "중": 20, "소": 9}),
        DefectItemDefinition("파손(외부)", "구조", {"대": 100}),
        DefectItemDefinition("블록이음부(이탈)", "구조", {"대": 35, "중": 17, "소": 6}),
        DefectItemDefinition("표면단차", "구조", {"대": 65, "중": 30, "소": 15}),
        DefectItemDefinition("블록이음부(손상)", "구조", {"대": 33, "중": 18, "소": 7}),
        DefectItemDefinition("뿌리침입", "운영", {"대": 60, "중": 33, "소": 13}),
        DefectItemDefinition("악취발생", "운영", {"소": 20}),
        DefectItemDefinition("침입수", "운영", {"대": 65, "중": 28, "소": 10}),
        DefectItemDefinition("사다리손상", "운영", {"대": 35, "중": 20, "소": 1}),
        DefectItemDefinition("폐유부착", "운영", {"대": 45, "중": 18, "소": 5}),
        DefectItemDefinition("임시장애물", "운영", {"대": 50, "중": 25, "소": 5}),
        DefectItemDefinition("뚜껑밀폐", "운영", {"중": 55}),
        DefectItemDefinition("내피생성", "운영", {"대": 40, "중": 15, "소": 3}),
    ],
    "관로": [
        DefectItemDefinition("좌굴", "구조", {"대": 70, "중": 40, "소": 20}),
        DefectItemDefinition("이음부단차", "구조", {"대": 70, "중": 40, "소": 20}),
        DefectItemDefinition("천공", "구조", {"대": 80, "중": 55, "소": 20}),
        DefectItemDefinition("균열(원주)", "구조", {"대": 40, "중": 15, "소": 5}),
        DefectItemDefinition("라이닝결함", "구조", {"대": 50, "중": 20, "소": 10}),
        DefectItemDefinition("영구장애물", "구조", {"대": 65, "중": 25, "소": 15}),
        DefectItemDefinition("연결관돌출", "구조", {"대": 25, "중": 10, "소": 3}),
        DefectItemDefinition("역경사", "구조", {"대": 58, "중": 28, "소": 18}),
        DefectItemDefinition("이음부손상", "구조", {"대": 33, "중": 18, "소": 7}),
        DefectItemDefinition("침하", "구조", {"대": 50, "중": 20, "소": 10}),
        DefectItemDefinition("연결관접합부", "구조", {"대": 30, "중": 10, "소": 3}),
        DefectItemDefinition("균열(길이)", "구조", {"대": 40, "중": 15, "소": 5}),
        DefectItemDefinition("표면손상", "구조", {"대": 50, "중": 20, "소": 10}),
        DefectItemDefinition("이음부이탈", "구조", {"대": 35, "중": 17, "소": 6}),
        DefectItemDefinition("균열(복합)", "구조", {"대": 45, "중": 20, "소": 10}),
        DefectItemDefinition("파손", "구조", {"대": 90, "소": 70}),
        DefectItemDefinition("붕괴", "구조", {"대": 100}),
        DefectItemDefinition("변형", "구조", {"대": 75, "중": 45, "소": 25}),
        DefectItemDefinition("폐유부착", "운영", {"대": 60, "중": 18, "소": 8}),
        DefectItemDefinition("내피생성", "운영", {"대": 40, "중": 12, "소": 1}),
        DefectItemDefinition("토사퇴적", "운영", {"대": 45, "중": 15, "소": 3}),
        DefectItemDefinition("임시장애물", "운영", {"대": 50, "중": 25, "소": 5}),
        DefectItemDefinition("침입수", "운영", {"대": 100, "중": 28, "소": 10}),
        DefectItemDefinition("뿌리침입", "운영", {"대": 65, "중": 33, "소": 13}),
    ],
    "암거": [
        DefectItemDefinition("연결관돌출", "구조", {"대": 25, "중": 10, "소": 3}),
        DefectItemDefinition("영구장애물", "구조", {"대": 65, "중": 25, "소": 15}),
        DefectItemDefinition("연결관접합부", "구조", {"대": 30, "중": 10, "소": 3}),
        DefectItemDefinition("파손", "구조", {"대": 90}),
        DefectItemDefinition("역경사", "구조", {"대": 58, "중": 28, "소": 10}),
        DefectItemDefinition("표면손상", "구조", {"대": 60, "중": 30, "소": 9}),
        DefectItemDefinition("침하", "구조", {"대": 50, "중": 20, "소": 10}),
        DefectItemDefinition("균열(복합)", "구조", {"대": 45, "중": 20, "소": 10}),
        DefectItemDefinition("이음부단차", "구조", {"대": 70, "중": 40, "소": 20}),
        DefectItemDefinition("균열(사선)", "구조", {"대": 43, "중": 18, "소": 6}),
        DefectItemDefinition("이음부이탈", "구조", {"대": 30, "중": 10, "소": 3}),
        DefectItemDefinition("균열(수직)", "구조", {"대": 38, "중": 15, "소": 5}),
        DefectItemDefinition("균열(길이)", "구조", {"대": 40, "중": 16, "소": 5}),
        DefectItemDefinition("이음부손상", "구조", {"대": 33, "중": 18, "소": 7}),
        DefectItemDefinition("붕괴", "구조", {"대": 100}),
        DefectItemDefinition("천공", "구조", {"대": 80, "중": 55, "소": 19}),
        DefectItemDefinition("뿌리침입", "운영", {"대": 60, "중": 33, "소": 13}),
        DefectItemDefinition("침입수", "운영", {"대": 50, "중": 28, "소": 10}),
        DefectItemDefinition("토사퇴적", "운영", {"대": 30, "중": 15, "소": 3}),
        DefectItemDefinition("임시장애물", "운영", {"대": 40, "중": 25, "소": 5}),
    ],
}


def condition_items_for_category(category: str) -> list[str]:
    return list(CONDITION_ITEMS_BY_CATEGORY.get(category, PIPE_CONDITION_ITEMS))


def defect_definitions_for_category(category: str) -> list[DefectItemDefinition]:
    return list(DEFECT_ITEMS_BY_CATEGORY.get(category, DEFECT_ITEMS_BY_CATEGORY["관로"]))


def defect_definition(category: str, defect_item: str) -> DefectItemDefinition | None:
    for definition in defect_definitions_for_category(category):
        if definition.item == defect_item:
            return definition
    return None


def grades_for_defect(category: str, defect_item: str) -> list[str]:
    definition = defect_definition(category, defect_item)
    if definition is None:
        return GRADE_ORDER.copy()
    return [grade for grade in GRADE_ORDER if grade in definition.scores]


def defect_score(category: str, defect_item: str, grade: str) -> int | None:
    definition = defect_definition(category, defect_item)
    if definition is None:
        return None
    return definition.scores.get(grade)
