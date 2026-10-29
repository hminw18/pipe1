from pathlib import Path

from PIL import Image as PILImage
from pipe1_core.services.pdf_report_service import (
    DefectItem,
    _defect_caption,
    _to_defect_item,
    generate_pipe_pdf_report,
)


def _make_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    img = PILImage.new("RGB", size, color)
    img.save(path, format="PNG")


def test_defect_caption_labels_only_the_present_item_type() -> None:
    condition_caption = _defect_caption(
        DefectItem(
            distance_m=7.5,
            grade="",
            image_path="",
            condition_item="조사완료(순방향)",
            defect_item="",
            timestamp_ms=65000,
        )
    )
    defect_caption = _defect_caption(
        DefectItem(
            distance_m=12.0,
            grade="대",
            image_path="",
            condition_item="",
            defect_item="균열(길이)",
            timestamp_ms=None,
        )
    )

    assert condition_caption == "01:05 | 7.50m | 조사완료(순방향) (상태)"
    assert defect_caption == "00:00 | 12.00m | 균열(길이) (이상) | 대"
    assert "||" not in condition_caption
    assert "||" not in defect_caption


def test_to_defect_item_does_not_treat_memo_as_defect_item() -> None:
    item = _to_defect_item(
        {
            "distance_m": 3.0,
            "grade": "",
            "image_path": "",
            "condition_item": "조사완료(순방향)",
            "defect_item": "",
            "memo": "현장 메모",
        }
    )

    assert item.defect_item == ""
    assert _defect_caption(item) == "00:00 | 3.00m | 조사완료(순방향) (상태)"


def test_generate_pipe_pdf_report_basic(tmp_path: Path) -> None:
    pipe_png = tmp_path / "pipe.png"
    defect_png = tmp_path / "defect.png"
    _make_png(pipe_png, (300, 1200), (200, 200, 200))
    _make_png(defect_png, (640, 360), (120, 120, 220))

    pdf_path = generate_pipe_pdf_report(
        project_name="Project-1",
        zone_name="Business-1",
        pipe_code="PIPE-001",
        pipe_length_m=20.0,
        pipe_png_path=str(pipe_png),
        defects_before=[
            {
                "distance_m": 5.0,
                "grade": "대",
                "image_path": str(defect_png),
                "condition_item": "구조특징",
                "defect_item": "균열(길이)",
            }
        ],
        defects_after=[
            {
                "distance_m": 10.0,
                "grade": "중",
                "image_path": str(defect_png),
                "condition_item": "조사완료(순방향)",
                "defect_item": "침입수",
            }
        ],
        output_path=str(tmp_path),
    )

    output = Path(pdf_path)
    assert output.exists()
    assert output.name == "PIPE-001_InspectionVisualReport.pdf"
    assert output.stat().st_size > 0


def test_generate_pipe_pdf_report_handles_missing_defect_images(tmp_path: Path) -> None:
    pdf_path = generate_pipe_pdf_report(
        project_name="Project-2",
        zone_name="Business-2",
        pipe_code="PIPE-002",
        pipe_length_m=40.0,
        pipe_png_path=str(tmp_path / "missing-pipe.png"),
        defects_before=[],
        defects_after=[
            {
                "distance_m": 7.5,
                "grade": "소",
                "image_path": str(tmp_path / "missing.png"),
                "condition_item": "기타",
                "defect_item": "표면손상",
            }
        ],
        output_path=str(tmp_path),
    )

    output = Path(pdf_path)
    assert output.exists()
    assert output.stat().st_size > 0


def test_generate_pipe_pdf_report_comparison_name(tmp_path: Path) -> None:
    pipe_png = tmp_path / "pipe.png"
    defect_png = tmp_path / "defect.png"
    _make_png(pipe_png, (300, 1200), (180, 180, 180))
    _make_png(defect_png, (640, 360), (120, 160, 220))

    pdf_path = generate_pipe_pdf_report(
        project_name="Project-3",
        zone_name="Business-3",
        pipe_code="PIPE-003",
        pipe_length_m=40.0,
        pipe_png_path=str(pipe_png),
        defects_before=[
            {
                "distance_m": 14.0,
                "grade": "중",
                "image_path": str(defect_png),
                "condition_item": "보수전",
                "defect_item": "균열",
                "timestamp_ms": 120000,
            }
        ],
        defects_after=[
            {
                "distance_m": 14.0,
                "grade": "소",
                "image_path": str(defect_png),
                "condition_item": "보수후",
                "defect_item": "보수완료",
                "timestamp_ms": 180000,
            }
        ],
        output_path=str(tmp_path),
        report_type="comparison",
    )

    output = Path(pdf_path)
    assert output.exists()
    assert output.name == "PIPE-003_ComparisonVisualReport.pdf"
