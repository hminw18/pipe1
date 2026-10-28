"""Defect taxonomy and fixed value lists for the UI (docs/Defect_Taxonomy.md)."""

from __future__ import annotations

from typing import get_args

from fastapi import APIRouter
from pipe1_core.defect_taxonomy import (
    GRADE_ORDER,
    ITEM_CATEGORIES,
    condition_code,
    condition_items_for_category,
    defect_code,
    defect_definitions_for_category,
)
from pydantic import BaseModel

from pipe1_server.api.defects import Quadrant
from pipe1_server.api.deps import CurrentSession
from pipe1_server.api.reports import UndrivenReason

router = APIRouter(prefix="/api", tags=["taxonomy"])


class ConditionItem(BaseModel):
    item: str
    code: str


class DefectItem(BaseModel):
    item: str
    code: str
    defect_type: str
    scores: dict[str, int]


class Category(BaseModel):
    category: str
    condition_items: list[ConditionItem]
    defect_items: list[DefectItem]


class Taxonomy(BaseModel):
    categories: list[Category]
    grades: list[str]
    quadrants: list[str]
    undriven_reasons: list[str]
    drive_directions: list[str]


@router.get("/taxonomy", response_model=Taxonomy)
def taxonomy(_: CurrentSession) -> Taxonomy:
    return Taxonomy(
        categories=[
            Category(
                category=category,
                condition_items=[
                    ConditionItem(item=item, code=condition_code(category, item))
                    for item in condition_items_for_category(category)
                ],
                defect_items=[
                    DefectItem(
                        item=d.item,
                        code=defect_code(category, d.item),
                        defect_type=d.defect_type,
                        scores=dict(d.scores),
                    )
                    for d in defect_definitions_for_category(category)
                ],
            )
            for category in ITEM_CATEGORIES
        ],
        grades=list(GRADE_ORDER),
        quadrants=list(get_args(Quadrant)),
        undriven_reasons=list(get_args(UndrivenReason)),
        drive_directions=["순주행", "역주행"],
    )
