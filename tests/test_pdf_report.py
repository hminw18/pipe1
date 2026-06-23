from pathlib import Path

from PIL import Image as PILImage

from sewerpipe_inspector.services.pdf_report_service import generate_pipe_pdf_report


def _make_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    img = PILImage.new("RGB", size, color)
    img.save(path, format="PNG")


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
    pipe_png = tmp_path / "pipe.png"
    _make_png(pipe_png, (300, 1200), (180, 180, 180))

    pdf_path = generate_pipe_pdf_report(
        project_name="Project-2",
        zone_name="Business-2",
        pipe_code="PIPE-002",
        pipe_length_m=40.0,
        pipe_png_path=str(pipe_png),
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
