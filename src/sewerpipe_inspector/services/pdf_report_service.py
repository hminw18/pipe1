from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Mapping, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
)

from sewerpipe_inspector.fonts import find_app_report_font_paths
from sewerpipe_inspector.resources import find_resource
from sewerpipe_inspector.state_grading import (
    StateGradeSummary,
    compute_pipe_state_grades,
    format_state_distance,
    format_state_section_label,
    format_state_time_range,
    format_state_value,
)

LOGGER = logging.getLogger(__name__)

PAGE_WIDTH_PT = float(A4[0])
PAGE_HEIGHT_PT = float(A4[1])
PAGE_MARGIN_PT = 40.0

PIPE_TOP_PT = 288.0
PIPE_BOTTOM_PT = 800.0
PIPE_HEIGHT_PT = PIPE_BOTTOM_PT - PIPE_TOP_PT
PIPE_IMAGE_HEIGHT_PT = PIPE_HEIGHT_PT
PIPE_IMAGE_WIDTH_PT = 58.0
CONNECTION_DIAGONAL_GUIDE_OFFSET_PT = 36.0
CONNECTION_START_MARKER_SIZE_PT = 3.4

TEMPLATE_CENTER_WIDTH_RATIO = 0.30
TEMPLATE_PIPE_WIDTH_RATIO = 0.995
TEMPLATE_PIPE_START_RATIO = 0.99
TEMPLATE_PIPE_END_RATIO = 0.01
TEMPLATE_PIPE_HEIGHT_RATIO = 1.0


@dataclass
class PipeTemplateConfig:
    center_width_ratio: float
    pipe_width_ratio: float
    pipe_start_ratio: float
    pipe_end_ratio: float
    pipe_height_ratio: float


DEFAULT_TEMPLATE_CONFIG = PipeTemplateConfig(
    center_width_ratio=TEMPLATE_CENTER_WIDTH_RATIO,
    pipe_width_ratio=TEMPLATE_PIPE_WIDTH_RATIO,
    pipe_start_ratio=TEMPLATE_PIPE_START_RATIO,
    pipe_end_ratio=TEMPLATE_PIPE_END_RATIO,
    pipe_height_ratio=TEMPLATE_PIPE_HEIGHT_RATIO,
)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _load_template_config(pipe_png_path: str) -> PipeTemplateConfig:
    candidates = [
        Path(pipe_png_path).with_name("pipe_template_calibration.json"),
        find_resource("pipe_template_calibration.json"),
        Path.cwd() / "pipe_template_calibration.json",
    ]
    for path in candidates:
        if path is None or not path.exists() or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.warning("Invalid calibration JSON: %s", path)
            continue

        try:
            center = _clamp(
                float(
                    raw.get(
                        "center_width_ratio", DEFAULT_TEMPLATE_CONFIG.center_width_ratio
                    )
                ),
                0.20,
                0.75,
            )
            pipe_w = _clamp(
                float(
                    raw.get(
                        "pipe_width_ratio", DEFAULT_TEMPLATE_CONFIG.pipe_width_ratio
                    )
                ),
                0.30,
                1.0,
            )
            start = _clamp(
                float(
                    raw.get(
                        "pipe_start_ratio", DEFAULT_TEMPLATE_CONFIG.pipe_start_ratio
                    )
                ),
                0.50,
                1.0,
            )
            end = _clamp(
                float(
                    raw.get("pipe_end_ratio", DEFAULT_TEMPLATE_CONFIG.pipe_end_ratio)
                ),
                0.0,
                0.50,
            )
            if start <= end:
                start = DEFAULT_TEMPLATE_CONFIG.pipe_start_ratio
                end = DEFAULT_TEMPLATE_CONFIG.pipe_end_ratio
            height = _clamp(
                float(
                    raw.get(
                        "pipe_height_ratio", DEFAULT_TEMPLATE_CONFIG.pipe_height_ratio
                    )
                ),
                0.70,
                1.0,
            )
            return PipeTemplateConfig(
                center_width_ratio=center,
                pipe_width_ratio=pipe_w,
                pipe_start_ratio=start,
                pipe_end_ratio=end,
                pipe_height_ratio=height,
            )
        except Exception:
            LOGGER.warning("Calibration parse failed: %s", path)
            continue

    return DEFAULT_TEMPLATE_CONFIG


@dataclass
class DefectItem:
    distance_m: float
    grade: str
    image_path: str
    condition_item: str
    defect_item: str
    timestamp_ms: int | None = None


@dataclass
class DefectBlockLayout:
    item: DefectItem
    top_offset: float
    image_width: float
    image_height: float
    block_height: float


@dataclass
class PipeDrawBounds:
    x: float
    y: float
    w: float
    h: float
    start_y: float
    end_y: float


@dataclass(frozen=True)
class ConnectionLineLayout:
    segments: tuple[tuple[float, float, float, float], ...]
    start_marker_x: float
    start_marker_y: float
    arrow_x: float
    arrow_y: float
    label_x: float
    label_y: float


def _connection_line_layout(
    *,
    side: str,
    pipe_x: float,
    pipe_w: float,
    image_x: float,
    image_y: float,
    image_w: float,
    image_h: float,
    pipe_point_y: float,
) -> ConnectionLineLayout:
    pipe_center_x = pipe_x + (pipe_w * 0.5)
    if side == "before":
        edge_x = pipe_x
        end_x = image_x + image_w
        guide_x = edge_x - CONNECTION_DIAGONAL_GUIDE_OFFSET_PT
    else:
        edge_x = pipe_x + pipe_w
        end_x = image_x
        guide_x = edge_x + CONNECTION_DIAGONAL_GUIDE_OFFSET_PT

    pipe_center_segment = (pipe_center_x, pipe_point_y, edge_x, pipe_point_y)

    image_bottom = image_y
    image_top = image_y + image_h
    if image_bottom <= pipe_point_y <= image_top:
        arrow_y = pipe_point_y
        return ConnectionLineLayout(
            segments=(
                pipe_center_segment,
                (edge_x, pipe_point_y, end_x, pipe_point_y),
            ),
            start_marker_x=pipe_center_x,
            start_marker_y=pipe_point_y,
            arrow_x=end_x,
            arrow_y=arrow_y,
            label_x=(edge_x + end_x) / 2.0,
            label_y=arrow_y - 8.0,
        )

    image_center_y = image_y + (image_h * 0.5)
    return ConnectionLineLayout(
        segments=(
            pipe_center_segment,
            (edge_x, pipe_point_y, guide_x, image_center_y),
            (guide_x, image_center_y, end_x, image_center_y),
        ),
        start_marker_x=pipe_center_x,
        start_marker_y=pipe_point_y,
        arrow_x=end_x,
        arrow_y=image_center_y,
        label_x=(guide_x + end_x) / 2.0,
        label_y=image_center_y - 8.0,
    )


class PipeVisualBodyFlowable(Flowable):
    def __init__(
        self,
        width: float,
        height: float,
        pipe_png_path: str,
        pipe_length_m: float,
        before_blocks: list[DefectBlockLayout],
        after_blocks: list[DefectBlockLayout],
        before_empty_message: Optional[str],
        after_empty_message: Optional[str],
        template_config: PipeTemplateConfig,
        normal_font: str,
        bold_font: str,
        is_comparison: bool,
    ) -> None:
        super().__init__()
        self.width = width
        self.height = height
        self.pipe_png_path = pipe_png_path
        self.pipe_length_m = pipe_length_m
        self.before_blocks = before_blocks
        self.after_blocks = after_blocks
        self.before_empty_message = before_empty_message
        self.after_empty_message = after_empty_message
        self.template_config = template_config
        self.normal_font = normal_font
        self.bold_font = bold_font
        self.is_comparison = is_comparison
        self._origin_x = 0.0
        self._origin_y = 0.0

        self.column_gap = 6 * mm
        self.title_height = 8 * mm if self.is_comparison else 0.0
        self.meta_line_height = 5 * mm
        self.block_gap = 0.2 * mm
        self.placeholder_height = 28 * mm
        center_width = self.width * self.template_config.center_width_ratio
        side_total = self.width - center_width - (2 * self.column_gap)
        self.side_width = side_total / 2.0
        self.center_width = center_width

        self.left_x = 0
        self.center_x = self.left_x + self.side_width + self.column_gap
        self.right_x = self.center_x + self.center_width + self.column_gap
        self.content_height = self.height - self.title_height

        styles = getSampleStyleSheet()
        self.title_style = ParagraphStyle(
            "column-title",
            parent=styles["Heading4"],
            alignment=1,
            fontName=self.bold_font,
            fontSize=10,
            leading=12,
            spaceAfter=0,
            spaceBefore=0,
        )
        self.small_style = ParagraphStyle(
            "defect-meta",
            parent=styles["Normal"],
            fontName=self.normal_font,
            fontSize=8,
            leading=10,
            spaceAfter=0,
            spaceBefore=0,
        )
        self.empty_style = ParagraphStyle(
            "empty-msg",
            parent=styles["Normal"],
            alignment=1,
            fontName=self.normal_font,
            textColor=colors.gray,
            fontSize=10,
        )

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        return self.width, self.height

    def drawOn(self, canv, x, y, _sW=0):
        self._origin_x = float(x)
        self._origin_y = float(y)
        return super().drawOn(canv, x, y)

    def draw(self) -> None:
        if self.is_comparison:
            self._draw_column_title("보수전", self.left_x, self.side_width)
            self._draw_column_title("보수후", self.right_x, self.side_width)

        pipe_bounds = self._draw_pipe_visual()
        self._draw_defect_side(
            side="before",
            start_x=self.left_x,
            column_width=self.side_width,
            layouts=self.before_blocks,
            empty_message=self.before_empty_message,
            pipe_bounds=pipe_bounds,
        )
        self._draw_defect_side(
            side="after",
            start_x=self.right_x,
            column_width=self.side_width,
            layouts=self.after_blocks,
            empty_message=self.after_empty_message,
            pipe_bounds=pipe_bounds,
        )

    def _draw_column_title(self, title: str, x: float, width: float) -> None:
        p = Paragraph(title, self.title_style)
        p_w, p_h = p.wrap(width, self.title_height)
        p.drawOn(self.canv, x + (width - p_w) / 2.0, self.height - p_h)

    def _draw_pipe_visual(self) -> PipeDrawBounds:
        pipe_area_bottom = (PAGE_HEIGHT_PT - PIPE_BOTTOM_PT) - self._origin_y

        draw_h = PIPE_IMAGE_HEIGHT_PT
        draw_w = PIPE_IMAGE_WIDTH_PT
        draw_x = (PAGE_WIDTH_PT * 0.5) - (draw_w * 0.5) - self._origin_x
        draw_y = pipe_area_bottom

        self.canv.saveState()
        self.canv.setFillColor(colors.HexColor("#D7D7D7"))
        self.canv.setStrokeColor(colors.HexColor("#B6B6B6"))
        self.canv.setLineWidth(0.6)
        self.canv.rect(draw_x, draw_y, draw_w, draw_h, stroke=1, fill=1)
        self.canv.restoreState()

        center_text = Paragraph(
            f"<b>연장</b><br/><b>{self.pipe_length_m:.2f} m</b>",
            ParagraphStyle(
                "pipe-center-text",
                parent=self.small_style,
                alignment=1,
                fontName=self.bold_font,
                textColor=colors.darkblue,
                fontSize=10,
                leading=12,
            ),
        )
        text_w, text_h = center_text.wrap(draw_w * 0.88, draw_h * 0.88)
        text_x = draw_x + (draw_w - text_w) / 2.0
        text_y = draw_y + (draw_h - text_h) / 2.0
        center_text.drawOn(self.canv, text_x, text_y)

        return PipeDrawBounds(
            x=draw_x,
            y=draw_y,
            w=draw_w,
            h=draw_h,
            start_y=draw_y + draw_h,
            end_y=draw_y,
        )

    def _draw_defect_side(
        self,
        side: str,
        start_x: float,
        column_width: float,
        layouts: list[DefectBlockLayout],
        empty_message: Optional[str],
        pipe_bounds: Optional[PipeDrawBounds],
    ) -> None:
        content_top = self.height - self.title_height
        content_bottom = content_top - self.content_height

        if not layouts and empty_message:
            p = Paragraph(empty_message, self.empty_style)
            p_w, p_h = p.wrap(column_width, self.content_height)
            p.drawOn(
                self.canv,
                start_x + (column_width - p_w) / 2.0,
                content_bottom + (self.content_height - p_h) / 2.0,
            )
            return

        for layout in layouts:
            block_top = content_top - layout.top_offset
            block_bottom = block_top - layout.block_height

            image_x = start_x + (column_width - layout.image_width) / 2.0
            image_y = block_top - layout.image_height
            image_drawn = self._safe_draw_image(
                layout.item.image_path,
                image_x,
                image_y,
                layout.image_width,
                layout.image_height,
            )

            caption_y = image_y - self.block_gap - 8.0
            caption_y = max(content_bottom + 1.0, caption_y)
            caption = _defect_caption(layout.item)
            max_chars = 62
            if len(caption) > max_chars:
                caption = caption[: max_chars - 1] + "..."
            self.canv.saveState()
            self.canv.setFont(self.small_style.fontName, 8.2)
            self.canv.setFillColor(colors.black)
            self.canv.drawCentredString(
                start_x + (column_width * 0.5), caption_y, caption
            )
            self.canv.restoreState()

            if not image_drawn:
                placeholder = Paragraph("Image missing", self.small_style)
                p_w, p_h = placeholder.wrap(layout.image_width, self.placeholder_height)
                placeholder.drawOn(
                    self.canv,
                    image_x + (layout.image_width - p_w) / 2.0,
                    image_y + (layout.image_height - p_h) / 2.0,
                )

            if pipe_bounds is not None:
                self._draw_connection_arrow(
                    side=side,
                    defect=layout.item,
                    image_x=image_x,
                    image_y=image_y,
                    image_w=layout.image_width,
                    image_h=layout.image_height,
                    pipe_bounds=pipe_bounds,
                )

    def _draw_connection_arrow(
        self,
        side: str,
        defect: DefectItem,
        image_x: float,
        image_y: float,
        image_w: float,
        image_h: float,
        pipe_bounds: PipeDrawBounds,
    ) -> None:
        pipe_x, _pipe_y, pipe_w, _pipe_h = (
            pipe_bounds.x,
            pipe_bounds.y,
            pipe_bounds.w,
            pipe_bounds.h,
        )
        if self.pipe_length_m > 0:
            ratio = max(0.0, min(1.0, defect.distance_m / self.pipe_length_m))
        else:
            ratio = 0.0

        y_from_top = PIPE_TOP_PT + (ratio * PIPE_HEIGHT_PT)
        abs_y = PAGE_HEIGHT_PT - y_from_top
        pipe_point_y = abs_y - self._origin_y
        effective_top = pipe_bounds.start_y
        effective_bottom = pipe_bounds.end_y
        pipe_point_y = max(effective_bottom, min(effective_top, pipe_point_y))

        line_layout = _connection_line_layout(
            side=side,
            pipe_x=pipe_x,
            pipe_w=pipe_w,
            image_x=image_x,
            image_y=image_y,
            image_w=image_w,
            image_h=image_h,
            pipe_point_y=pipe_point_y,
        )

        self.canv.saveState()
        self.canv.setStrokeColor(colors.HexColor("#44618E"))
        self.canv.setLineWidth(0.8)
        for x1, y1, x2, y2 in line_layout.segments:
            self.canv.line(x1, y1, x2, y2)
        marker_size = CONNECTION_START_MARKER_SIZE_PT
        self.canv.setFillColor(colors.HexColor("#44618E"))
        self.canv.rect(
            line_layout.start_marker_x - (marker_size / 2.0),
            line_layout.start_marker_y - (marker_size / 2.0),
            marker_size,
            marker_size,
            stroke=1,
            fill=1,
        )

        self.canv.setFont(self.normal_font, 7.0)
        self.canv.setFillColor(colors.HexColor("#263F66"))
        self.canv.drawCentredString(
            line_layout.label_x,
            line_layout.label_y,
            f"{defect.distance_m:.2f}m",
        )

        arrow_size = 3.2
        if side == "before":
            self.canv.line(
                line_layout.arrow_x,
                line_layout.arrow_y,
                line_layout.arrow_x - arrow_size,
                line_layout.arrow_y + arrow_size * 0.6,
            )
            self.canv.line(
                line_layout.arrow_x,
                line_layout.arrow_y,
                line_layout.arrow_x - arrow_size,
                line_layout.arrow_y - arrow_size * 0.6,
            )
        else:
            self.canv.line(
                line_layout.arrow_x,
                line_layout.arrow_y,
                line_layout.arrow_x + arrow_size,
                line_layout.arrow_y + arrow_size * 0.6,
            )
            self.canv.line(
                line_layout.arrow_x,
                line_layout.arrow_y,
                line_layout.arrow_x + arrow_size,
                line_layout.arrow_y - arrow_size * 0.6,
            )
        self.canv.restoreState()

    def _safe_draw_image(
        self,
        image_path: str,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> bool:
        path = Path(image_path)
        if not path.exists():
            LOGGER.warning("Defect image missing: %s", image_path)
            self.canv.setStrokeColor(colors.lightgrey)
            self.canv.rect(x, y, width, height, stroke=1, fill=0)
            return False
        try:
            img = Image(str(path), width=width, height=height)
            img.drawOn(self.canv, x, y)
            return True
        except Exception:
            LOGGER.warning("Defect image unreadable: %s", image_path)
            self.canv.setStrokeColor(colors.lightgrey)
            self.canv.rect(x, y, width, height, stroke=1, fill=0)
            return False


def _to_defect_item(raw: dict) -> DefectItem:
    return DefectItem(
        distance_m=float(raw.get("distance_m", 0.0)),
        grade=str(raw.get("grade") or ""),
        image_path=str(raw.get("image_path", "")),
        condition_item=str(raw.get("condition_item") or raw.get("defect_type") or ""),
        defect_item=str(raw.get("defect_item") or ""),
        timestamp_ms=(
            None if raw.get("timestamp_ms") is None else int(raw.get("timestamp_ms", 0))
        ),
    )


def _format_mmss(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "00:00"
    seconds = max(0, int(timestamp_ms) // 1000)
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def _defect_caption(item: DefectItem) -> str:
    parts = [
        _format_mmss(item.timestamp_ms),
        f"{item.distance_m:.2f}m",
    ]
    if item.defect_item:
        parts.append(f"{item.defect_item} (이상)")
    elif item.condition_item:
        parts.append(f"{item.condition_item} (상태)")
    if item.grade:
        parts.append(item.grade)
    return " | ".join(parts)


def _safe_image_size(image_path: str) -> tuple[float, float]:
    path = Path(image_path)
    if not path.exists():
        return 4.0, 3.0
    try:
        reader = ImageReader(str(path))
        w, h = reader.getSize()
        if w <= 0 or h <= 0:
            return 4.0, 3.0
        return float(w), float(h)
    except Exception:
        return 4.0, 3.0


def _paginate_defect_layouts(
    defects: list[DefectItem],
    column_width: float,
    content_height: float,
    _pipe_length_m: float,
) -> list[list[DefectBlockLayout]]:
    if not defects:
        return [[]]

    caption_height = 10.0
    block_gap = 0.2 * mm
    max_per_page = 4
    ordered = sorted(defects, key=lambda d: d.distance_m)

    pages: list[list[DefectBlockLayout]] = []

    for start_index in range(0, len(ordered), max_per_page):
        chunk = ordered[start_index : start_index + max_per_page]
        slot_height = content_height / max_per_page
        text_height = caption_height
        max_image_width = min(240 * mm, column_width)
        slot_image_max_h = max(8 * mm, slot_height - text_height - block_gap - (2 * mm))

        page_layouts: list[DefectBlockLayout] = []
        for idx, item in enumerate(chunk):
            src_w, src_h = _safe_image_size(item.image_path)
            aspect = src_h / src_w
            fit_by_height_w = slot_image_max_h / max(aspect, 0.01)
            image_width = max(10 * mm, min(max_image_width, fit_by_height_w * 2.0))
            image_height = image_width * aspect
            block_height = image_height + text_height + block_gap
            slot_top = idx * slot_height
            top_offset = slot_top + max(0.0, (slot_height - block_height) / 2.0)

            page_layouts.append(
                DefectBlockLayout(
                    item=item,
                    top_offset=top_offset,
                    image_width=image_width,
                    image_height=image_height,
                    block_height=block_height,
                )
            )

        pages.append(page_layouts)

    return pages


def _layout_defect_block(
    item: DefectItem,
    slot_index: int,
    slot_height: float,
    column_width: float,
) -> DefectBlockLayout:
    caption_height = 10.0
    block_gap = 0.2 * mm
    text_height = caption_height
    max_image_width = min(240 * mm, column_width)
    slot_image_max_h = max(8 * mm, slot_height - text_height - block_gap - (2 * mm))
    src_w, src_h = _safe_image_size(item.image_path)
    aspect = src_h / src_w
    fit_by_height_w = slot_image_max_h / max(aspect, 0.01)
    image_width = max(10 * mm, min(max_image_width, fit_by_height_w * 2.0))
    image_height = image_width * aspect
    block_height = image_height + text_height + block_gap
    slot_top = slot_index * slot_height
    top_offset = slot_top + max(0.0, (slot_height - block_height) / 2.0)
    return DefectBlockLayout(
        item=item,
        top_offset=top_offset,
        image_width=image_width,
        image_height=image_height,
        block_height=block_height,
    )


def _paginate_single_alternating_layouts(
    defects: list[DefectItem],
    column_width: float,
    content_height: float,
) -> list[tuple[list[DefectBlockLayout], list[DefectBlockLayout]]]:
    if not defects:
        return [([], [])]

    max_rows_per_page = 4
    max_items_per_page = max_rows_per_page * 2
    slot_height = content_height / max_rows_per_page
    ordered = sorted(defects, key=lambda d: d.distance_m)
    pages: list[tuple[list[DefectBlockLayout], list[DefectBlockLayout]]] = []

    for page_start in range(0, len(ordered), max_items_per_page):
        chunk = ordered[page_start : page_start + max_items_per_page]
        left_blocks: list[DefectBlockLayout] = []
        right_blocks: list[DefectBlockLayout] = []
        for idx, item in enumerate(chunk):
            slot_index = idx // 2
            layout = _layout_defect_block(item, slot_index, slot_height, column_width)
            if idx % 2 == 0:
                left_blocks.append(layout)
            else:
                right_blocks.append(layout)
        pages.append((left_blocks, right_blocks))

    return pages


def _paginate_comparison_layouts(
    before_items: list[DefectItem],
    after_items: list[DefectItem],
    column_width: float,
    content_height: float,
) -> list[tuple[list[DefectBlockLayout], list[DefectBlockLayout]]]:
    if not before_items and not after_items:
        return [([], [])]

    grouped: dict[float, tuple[list[DefectItem], list[DefectItem]]] = {}
    for item in sorted(before_items, key=lambda d: d.distance_m):
        key = round(item.distance_m, 2)
        grouped.setdefault(key, ([], []))[0].append(item)
    for item in sorted(after_items, key=lambda d: d.distance_m):
        key = round(item.distance_m, 2)
        grouped.setdefault(key, ([], []))[1].append(item)

    rows: list[tuple[DefectItem | None, DefectItem | None]] = []
    for distance in sorted(grouped):
        before_group, after_group = grouped[distance]
        max_count = max(len(before_group), len(after_group))
        for idx in range(max_count):
            rows.append(
                (
                    before_group[idx] if idx < len(before_group) else None,
                    after_group[idx] if idx < len(after_group) else None,
                )
            )

    max_rows_per_page = 4
    slot_height = content_height / max_rows_per_page
    pages: list[tuple[list[DefectBlockLayout], list[DefectBlockLayout]]] = []
    for page_start in range(0, len(rows), max_rows_per_page):
        chunk = rows[page_start : page_start + max_rows_per_page]
        before_blocks: list[DefectBlockLayout] = []
        after_blocks: list[DefectBlockLayout] = []
        for slot_index, (before_item, after_item) in enumerate(chunk):
            if before_item is not None:
                before_blocks.append(
                    _layout_defect_block(
                        before_item, slot_index, slot_height, column_width
                    )
                )
            if after_item is not None:
                after_blocks.append(
                    _layout_defect_block(
                        after_item, slot_index, slot_height, column_width
                    )
                )
        pages.append((before_blocks, after_blocks))

    return pages


def _make_header_table(
    project_name: str,
    zone_name: str,
    pipe_code: str,
    doc_width: float,
) -> Table:
    header_data = [
        ["Project", project_name],
        ["Business", zone_name],
        ["Report", pipe_code],
    ]
    table = Table(header_data, colWidths=[24 * mm, doc_width - (24 * mm)])
    table.setStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.lightgrey),
        ]
    )
    return table


def _register_korean_fonts() -> tuple[str, str]:
    app_report_font_paths = find_app_report_font_paths()
    candidates = [
        app_report_font_paths,
        (Path.cwd() / "font.ttf", Path.cwd() / "font.ttf"),
        (Path.cwd() / "font.TTF", Path.cwd() / "font.TTF"),
        (
            Path.cwd() / "fonts" / "NanumGothic.ttf",
            Path.cwd() / "fonts" / "NanumGothicBold.ttf",
        ),
        (
            Path("/Library/Fonts/NanumGothic.ttf"),
            Path("/Library/Fonts/NanumGothicBold.ttf"),
        ),
        (
            Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
            Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        ),
    ]

    for candidate in candidates:
        if candidate is None:
            continue
        normal_path, bold_path = candidate
        if not normal_path.exists():
            continue
        normal_name = "KoreanReport"
        bold_name = "KoreanReportBold"
        if normal_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(normal_name, str(normal_path)))
        if bold_path.exists() and bold_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
        elif (
            not bold_path.exists()
            and bold_name not in pdfmetrics.getRegisteredFontNames()
        ):
            pdfmetrics.registerFont(TTFont(bold_name, str(normal_path)))
        return normal_name, bold_name

    return "Helvetica", "Helvetica-Bold"


def _row_value(
    row: Mapping[str, object] | None,
    key: str,
    default: object = "",
) -> object:
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _number_text(value: object, suffix: str = "", default: str = "") -> str:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value, default)
    return f"{number:.2f}{suffix}"


def _compact_number_text(value: object, default: str = "-") -> str:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value, default)
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _period_text(start: object, end: object) -> str:
    start_text = _text(start)
    end_text = _text(end)
    if start_text and end_text:
        return f"{start_text} - {end_text}"
    return start_text or end_text


def _location_text(data: Mapping[str, object]) -> str:
    road_address = _text(data.get("road_address"))
    if road_address:
        return road_address
    return " ".join(
        part
        for part in [
            _text(data.get("province")),
            _text(data.get("city_county")),
            _text(data.get("town")),
            _text(data.get("village")),
            _text(data.get("lot_number")),
        ]
        if part
    )


def _paragraph(text: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_text(text)), style)


def build_pdf_header_data(
    project_name: str,
    zone_name: str,
    pipe_code: str,
    pipe_length_m: float,
    report_context: Mapping[str, object] | None = None,
    pipe_info: Mapping[str, object] | None = None,
    upstream_manhole: Mapping[str, object] | None = None,
    downstream_manhole: Mapping[str, object] | None = None,
    actual_survey: Mapping[str, object] | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "project_name": project_name,
        "business_name": zone_name,
        "report_number": pipe_code,
        "pipe_number": pipe_code,
        "treatment_area": zone_name,
        "pipe_length_m": pipe_length_m,
    }

    if report_context is not None:
        for key in report_context.keys():
            data[key] = report_context[key]

    data["project_name"] = _text(data.get("project_name"), project_name)
    data["business_name"] = _text(data.get("business_name"), zone_name)
    data["report_number"] = _text(data.get("report_number"), pipe_code)
    data["pipe_number"] = _text(data.get("pipe_number"), data["report_number"])
    data["business_period"] = _period_text(
        data.get("business_start_date"),
        data.get("business_end_date"),
    )
    data["location"] = _location_text(data)

    length = _row_value(pipe_info, "length_m", pipe_length_m)
    total_drive = _row_value(pipe_info, "total_drive_distance_m", "")
    undriven = _row_value(pipe_info, "undriven_distance_m", "")
    data["length_m"] = _number_text(length, " m")
    data["total_drive_distance_m"] = _number_text(total_drive, " m")
    data["undriven_distance_m"] = _number_text(undriven, " m")

    for prefix, row in (
        ("upstream", upstream_manhole),
        ("downstream", downstream_manhole),
    ):
        for key in [
            "manhole_number",
            "manhole_type",
            "internal_material",
            "cover_material",
            "el_m",
            "size",
            "depth_m",
            "invert",
            "ladder_shape",
            "latitude",
            "longitude",
        ]:
            data[f"{prefix}_{key}"] = _row_value(row, key, "")

    data["start_occurrence_point_m"] = _compact_number_text(
        _row_value(actual_survey, "start_occurrence_point_m", None)
    )
    data["start_undriven_reason"] = _text(
        _row_value(actual_survey, "start_undriven_reason", "없음"),
        "없음",
    )
    data["start_undriven_reason_detail"] = _text(
        _row_value(actual_survey, "start_undriven_reason_detail", "")
    )
    data["end_occurrence_point_m"] = _compact_number_text(
        _row_value(actual_survey, "end_occurrence_point_m", None)
    )
    data["end_undriven_reason"] = _text(
        _row_value(actual_survey, "end_undriven_reason", "없음"),
        "없음",
    )
    data["end_undriven_reason_detail"] = _text(
        _row_value(actual_survey, "end_undriven_reason_detail", "")
    )
    data["survey_content"] = _text(_row_value(actual_survey, "survey_content", ""))
    return data


def generate_header_table(data: Mapping[str, object]) -> Table:
    normal_font, bold_font = _register_korean_fonts()
    total_width = PAGE_WIDTH_PT - (PAGE_MARGIN_PT * 2.0)
    col_widths = [total_width / 12.0] * 12

    styles = getSampleStyleSheet()
    label_style = ParagraphStyle(
        "h-label",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=6.0,
        leading=7.0,
        alignment=1,
        wordWrap="CJK",
        splitLongWords=0,
    )
    value_style = ParagraphStyle(
        "h-value",
        parent=styles["Normal"],
        fontName=normal_font,
        fontSize=6.3,
        leading=7.2,
        wordWrap="CJK",
    )
    center_value_style = ParagraphStyle(
        "h-value-center",
        parent=value_style,
        alignment=1,
    )
    subhead_style = ParagraphStyle(
        "h-sub",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=5.8,
        leading=6.8,
        alignment=1,
        wordWrap="CJK",
    )

    def l(text: str) -> Paragraph:
        return _paragraph(text, label_style)

    def v(key: str, default: str = "", centered: bool = False) -> Paragraph:
        style = center_value_style if centered else value_style
        return _paragraph(data.get(key, default), style)

    rows: list[list[Paragraph]] = [
        [_paragraph("", value_style) for _ in range(12)] for _ in range(17)
    ]

    rows[0][0] = l("사업명")
    rows[0][1] = v("project_name")
    rows[0][8] = l("보고서번호")
    rows[0][9] = v("report_number")

    rows[1][0] = l("발주처")
    rows[1][1] = v("client")
    rows[1][4] = l("매설년도")
    rows[1][5] = v("buried_years")
    rows[1][8] = l("사업기간")
    rows[1][9] = v("business_period")

    rows[2][0] = l("처리구역")
    rows[2][1] = v("treatment_area")
    rows[2][4] = l("배수구역")
    rows[2][5] = v("drainage_area")
    rows[2][8] = l("배수분구")
    rows[2][9] = v("drainage_district")

    rows[3][0] = l("조사위치")
    rows[3][1] = v("location")
    rows[3][8] = l("시공자")
    rows[3][9] = v("contractor")

    rows[4][0] = l("조사목적")
    rows[4][1] = v("survey_purpose")
    rows[4][4] = l("조사일자")
    rows[4][5] = v("survey_date")
    rows[4][8] = l("조사자")
    rows[4][9] = v("inspector")

    for col, text in [
        (0, "관로번호"),
        (2, "구분"),
        (3, "관종"),
        (5, "규격"),
        (6, "배수방식"),
        (8, "연장"),
        (9, "총주행거리"),
        (11, "미주행거리"),
    ]:
        rows[5][col] = _paragraph(text, subhead_style)

    rows[6][0] = v("pipe_number", centered=True)
    rows[6][2] = v("category", centered=True)
    rows[6][3] = v("pipe_type", centered=True)
    rows[6][5] = v("specification", centered=True)
    rows[6][6] = v("drain_type", centered=True)
    rows[6][8] = v("length_m", centered=True)
    rows[6][9] = v("total_drive_distance_m", centered=True)
    rows[6][11] = v("undriven_distance_m", centered=True)

    manhole_headers = [
        "맨홀부",
        "맨홀번호",
        "맨홀종류",
        "맨홀내부재질",
        "맨홀뚜껑재질",
        "맨홀 E.L",
        "맨홀크기",
        "맨홀깊이",
        "인버트",
        "사다리모양",
        "위도",
        "경도",
    ]
    for i, text in enumerate(manhole_headers):
        rows[7][i] = _paragraph(text, subhead_style)

    for row_index, prefix, label in (
        (8, "upstream", "상류맨홀"),
        (9, "downstream", "하류맨홀"),
    ):
        rows[row_index][0] = _paragraph(label, subhead_style)
        for col, key in enumerate(
            [
                "manhole_number",
                "manhole_type",
                "internal_material",
                "cover_material",
                "el_m",
                "size",
                "depth_m",
                "invert",
                "ladder_shape",
                "latitude",
                "longitude",
            ],
            start=1,
        ):
            rows[row_index][col] = v(f"{prefix}_{key}", centered=True)

    upstream_coord = (
        f"위도: {_text(data.get('upstream_latitude'), '-')} / "
        f"경도: {_text(data.get('upstream_longitude'), '-')}"
    )
    downstream_coord = (
        f"위도: {_text(data.get('downstream_latitude'), '-')} / "
        f"경도: {_text(data.get('downstream_longitude'), '-')}"
    )
    rows[10][0] = _paragraph("상류맨홀좌표", subhead_style)
    rows[10][2] = _paragraph(upstream_coord, center_value_style)
    rows[10][6] = _paragraph("하류맨홀좌표", subhead_style)
    rows[10][8] = _paragraph(downstream_coord, center_value_style)

    rows[11][0] = _paragraph("조사시점맨홀 구조적 상태등급", subhead_style)
    rows[11][3] = v("start_manhole_structural_grade", centered=True)
    rows[11][6] = _paragraph("조사지점맨홀 운영적 상태등급", subhead_style)
    rows[11][9] = v("end_manhole_operational_grade", centered=True)

    rows[12][0] = _paragraph("하수관로(암거) 구조적 상태등급", subhead_style)
    rows[12][3] = v("pipe_structural_grade", centered=True)
    rows[12][6] = _paragraph("하수관로(암거) 운영적 상태등급", subhead_style)
    rows[12][9] = v("pipe_operational_grade", centered=True)

    rows[13][0] = _paragraph("미주행방향", subhead_style)
    rows[13][3] = _paragraph("발생지점", subhead_style)
    rows[13][5] = _paragraph("미주행사유", subhead_style)
    rows[13][7] = _paragraph("미주행사유에 대한 설명 / 비고", subhead_style)

    rows[14][0] = _paragraph("상류->하류", center_value_style)
    rows[14][3] = v("start_occurrence_point_m", centered=True)
    rows[14][5] = v("start_undriven_reason", centered=True)
    rows[14][7] = v("start_undriven_reason_detail", centered=True)

    rows[15][0] = _paragraph("하류->상류", center_value_style)
    rows[15][3] = v("end_occurrence_point_m", centered=True)
    rows[15][5] = v("end_undriven_reason", centered=True)
    rows[15][7] = v("end_undriven_reason_detail", centered=True)

    rows[16][0] = _paragraph("조사내용", subhead_style)
    rows[16][1] = v("survey_content")

    table = Table(
        rows,
        colWidths=col_widths,
        rowHeights=[11.5] * 17,
        repeatRows=0,
    )
    table.setStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#8F8F8F")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 1.4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 1.4),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("SPAN", (1, 0), (7, 0)),
            ("SPAN", (9, 0), (11, 0)),
            ("SPAN", (1, 1), (3, 1)),
            ("SPAN", (5, 1), (7, 1)),
            ("SPAN", (9, 1), (11, 1)),
            ("SPAN", (1, 2), (3, 2)),
            ("SPAN", (5, 2), (7, 2)),
            ("SPAN", (9, 2), (11, 2)),
            ("SPAN", (1, 3), (7, 3)),
            ("SPAN", (9, 3), (11, 3)),
            ("SPAN", (1, 4), (3, 4)),
            ("SPAN", (5, 4), (7, 4)),
            ("SPAN", (9, 4), (11, 4)),
            ("SPAN", (0, 5), (1, 5)),
            ("SPAN", (3, 5), (4, 5)),
            ("SPAN", (6, 5), (7, 5)),
            ("SPAN", (9, 5), (10, 5)),
            ("SPAN", (0, 6), (1, 6)),
            ("SPAN", (3, 6), (4, 6)),
            ("SPAN", (6, 6), (7, 6)),
            ("SPAN", (9, 6), (10, 6)),
            ("SPAN", (0, 10), (1, 10)),
            ("SPAN", (2, 10), (5, 10)),
            ("SPAN", (6, 10), (7, 10)),
            ("SPAN", (8, 10), (11, 10)),
            ("SPAN", (0, 11), (2, 11)),
            ("SPAN", (3, 11), (5, 11)),
            ("SPAN", (6, 11), (8, 11)),
            ("SPAN", (9, 11), (11, 11)),
            ("SPAN", (0, 12), (2, 12)),
            ("SPAN", (3, 12), (5, 12)),
            ("SPAN", (6, 12), (8, 12)),
            ("SPAN", (9, 12), (11, 12)),
            ("SPAN", (0, 13), (2, 13)),
            ("SPAN", (3, 13), (4, 13)),
            ("SPAN", (5, 13), (6, 13)),
            ("SPAN", (7, 13), (11, 13)),
            ("SPAN", (0, 14), (2, 14)),
            ("SPAN", (3, 14), (4, 14)),
            ("SPAN", (5, 14), (6, 14)),
            ("SPAN", (7, 14), (11, 14)),
            ("SPAN", (0, 15), (2, 15)),
            ("SPAN", (3, 15), (4, 15)),
            ("SPAN", (5, 15), (6, 15)),
            ("SPAN", (7, 15), (11, 15)),
            ("SPAN", (1, 16), (11, 16)),
            ("BACKGROUND", (0, 0), (0, 4), colors.HexColor("#D9D9D9")),
            ("BACKGROUND", (4, 1), (4, 2), colors.HexColor("#D9D9D9")),
            ("BACKGROUND", (4, 4), (4, 4), colors.HexColor("#D9D9D9")),
            ("BACKGROUND", (8, 0), (8, 4), colors.HexColor("#D9D9D9")),
            ("BACKGROUND", (0, 5), (11, 5), colors.HexColor("#BFBFBF")),
            ("BACKGROUND", (0, 7), (11, 7), colors.HexColor("#BFBFBF")),
            ("BACKGROUND", (0, 10), (1, 10), colors.HexColor("#D9D9D9")),
            ("BACKGROUND", (0, 11), (2, 12), colors.HexColor("#D9D9D9")),
            ("BACKGROUND", (6, 10), (7, 10), colors.HexColor("#D9D9D9")),
            ("BACKGROUND", (6, 11), (8, 12), colors.HexColor("#D9D9D9")),
            ("BACKGROUND", (0, 13), (11, 13), colors.HexColor("#BFBFBF")),
            ("BACKGROUND", (0, 16), (0, 16), colors.HexColor("#D9D9D9")),
        ]
    )
    return table


def _make_state_grade_table(
    title: str,
    summary: StateGradeSummary,
    doc_width: float,
    normal_font: str,
    bold_font: str,
) -> list:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        f"state-title-{title}",
        parent=styles["Heading4"],
        fontName=bold_font,
        fontSize=11,
        leading=14,
        spaceAfter=4,
    )
    cell_style = ParagraphStyle(
        f"state-cell-{title}",
        parent=styles["Normal"],
        fontName=normal_font,
        fontSize=7.2,
        leading=8.5,
        alignment=1,
        wordWrap="CJK",
    )
    header_style = ParagraphStyle(
        f"state-header-{title}",
        parent=cell_style,
        fontName=bold_font,
    )

    flowables = [
        Paragraph(title, title_style),
        Table(
            [
                [
                    Paragraph("전체 구조등급", header_style),
                    Paragraph(format_state_value(summary.structural_grade), cell_style),
                    Paragraph("전체 운영등급", header_style),
                    Paragraph(format_state_value(summary.operational_grade), cell_style),
                ]
            ],
            colWidths=[doc_width * 0.18, doc_width * 0.18, doc_width * 0.18, doc_width * 0.18],
        ),
        Spacer(1, 3 * mm),
    ]

    headers = [
        "구간",
        "구간정보",
        "주행방향",
        "거리범위(m)",
        "구조점수",
        "구조등급",
        "운영점수",
        "운영등급",
        "결함수",
    ]
    rows = [[Paragraph(header, header_style) for header in headers]]
    for section in summary.sections:
        time_range = format_state_time_range(
            section.start_timestamp_ms, section.end_timestamp_ms
        )
        section_label = format_state_section_label(section)
        section_info = "<br/>".join(
            part for part in (time_range, section_label) if part
        )
        distance_range = (
            f"{format_state_distance(section.start_distance_m)}~"
            f"{format_state_distance(section.end_distance_m)}"
        )
        rows.append(
            [
                Paragraph(str(section.index), cell_style),
                Paragraph(section_info, cell_style),
                Paragraph(section.drive_direction, cell_style),
                Paragraph(distance_range, cell_style),
                Paragraph(str(section.structural_score), cell_style),
                Paragraph(str(section.structural_grade), cell_style),
                Paragraph(str(section.operational_score), cell_style),
                Paragraph(str(section.operational_grade), cell_style),
                Paragraph(str(section.defect_count), cell_style),
            ]
        )

    table = Table(
        rows,
        colWidths=[
            doc_width * 0.07,
            doc_width * 0.27,
            doc_width * 0.10,
            doc_width * 0.14,
            doc_width * 0.09,
            doc_width * 0.09,
            doc_width * 0.09,
            doc_width * 0.09,
            doc_width * 0.06,
        ],
        repeatRows=1,
    )
    table.setStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#8F8F8F")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9D9D9")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )
    flowables.append(table)
    return flowables


def _draw_page_footer(canvas, _doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(A4[0] / 2.0, 10 * mm, str(page_num))
    canvas.restoreState()


def generate_pipe_pdf_report(
    project_name: str,
    zone_name: str,
    pipe_code: str,
    pipe_length_m: float,
    pipe_png_path: str,
    defects_before: list[dict],
    defects_after: list[dict],
    output_path: str,
    report_type: str = "inspection",
    report_context: Mapping[str, object] | None = None,
    pipe_info: Mapping[str, object] | None = None,
    upstream_manhole: Mapping[str, object] | None = None,
    downstream_manhole: Mapping[str, object] | None = None,
    actual_survey: Mapping[str, object] | None = None,
) -> str:
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_type = report_type if report_type in {"inspection", "post_repair", "comparison"} else "inspection"
    filename_suffix = {
        "inspection": "InspectionVisualReport",
        "post_repair": "PostRepairVisualReport",
        "comparison": "ComparisonVisualReport",
    }[report_type]
    report_title = {
        "inspection": "하수관거 조사 보고서",
        "post_repair": "하수관거 보수후 보고서",
        "comparison": "하수관거 보수 비교 보고서",
    }[report_type]
    is_comparison = report_type == "comparison"
    pdf_path = output_dir / f"{pipe_code}_{filename_suffix}.pdf"

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=PAGE_MARGIN_PT,
        rightMargin=PAGE_MARGIN_PT,
        topMargin=PAGE_MARGIN_PT / 2.0,
        bottomMargin=PAGE_MARGIN_PT,
    )

    before_state_summary = compute_pipe_state_grades(defects_before)
    after_state_summary = compute_pipe_state_grades(defects_after)
    before_items = [_to_defect_item(item) for item in defects_before]
    after_items = [_to_defect_item(item) for item in defects_after]
    template_config = _load_template_config(pipe_png_path)

    normal_font, bold_font = _register_korean_fonts()
    header_data = build_pdf_header_data(
        project_name=project_name,
        zone_name=zone_name,
        pipe_code=pipe_code,
        pipe_length_m=pipe_length_m,
        report_context=report_context,
        pipe_info=pipe_info,
        upstream_manhole=upstream_manhole,
        downstream_manhole=downstream_manhole,
        actual_survey=actual_survey,
    )
    header_data["pipe_structural_grade"] = format_state_value(
        before_state_summary.structural_grade
    )
    header_data["pipe_operational_grade"] = format_state_value(
        before_state_summary.operational_grade
    )
    header_table_sample = generate_header_table(header_data)
    _w, header_table_height = header_table_sample.wrap(doc.width, doc.height)
    title_style = ParagraphStyle(
        "report-title",
        fontName=bold_font,
        fontSize=21,
        leading=24,
        alignment=1,
    )
    title_sample = Paragraph(report_title, title_style)
    _tw, title_height = title_sample.wrap(doc.width, doc.height)
    title_gap = 7.5
    body_top_spacer = 4 * mm
    first_page_header_height = title_height + title_gap + header_table_height

    visual_height = max(
        80 * mm, doc.height - first_page_header_height - body_top_spacer - (6 * mm)
    )
    column_gap = 6 * mm
    center_width = doc.width * template_config.center_width_ratio
    side_width = (doc.width - center_width - (2 * column_gap)) / 2.0
    content_height = visual_height - ((8 * mm) if is_comparison else 0.0)

    if is_comparison:
        visual_pages = _paginate_comparison_layouts(
            before_items=before_items,
            after_items=after_items,
            column_width=side_width,
            content_height=content_height,
        )
    else:
        visual_pages = _paginate_single_alternating_layouts(
            defects=before_items + after_items,
            column_width=side_width,
            content_height=content_height,
        )

    total_pages = max(len(visual_pages), 1)
    story = []

    for page_index in range(total_pages):
        before_blocks, after_blocks = visual_pages[page_index]

        body = PipeVisualBodyFlowable(
            width=doc.width,
            height=visual_height,
            pipe_png_path=pipe_png_path,
            pipe_length_m=pipe_length_m,
            before_blocks=before_blocks,
            after_blocks=after_blocks,
            before_empty_message=(
                "결함 사진 없음"
                if not before_items and not after_items and page_index == 0
                else None
            ),
            after_empty_message=(
                "결함 사진 없음"
                if is_comparison and not after_items and page_index == 0
                else None
            ),
            template_config=template_config,
            normal_font=normal_font,
            bold_font=bold_font,
            is_comparison=is_comparison,
        )
        section = []
        section.append(Paragraph(report_title, title_style))
        section.append(Spacer(1, title_gap))
        section.append(generate_header_table(header_data))
        section.append(Spacer(1, body_top_spacer))
        section.append(body)
        story.append(KeepTogether(section))

        if page_index < total_pages - 1:
            story.append(PageBreak())

    if before_state_summary.sections or after_state_summary.sections:
        story.append(PageBreak())
        if before_state_summary.sections:
            story.extend(
                _make_state_grade_table(
                    "단위구간 상태등급" if not is_comparison else "보수전 단위구간 상태등급",
                    before_state_summary,
                    doc.width,
                    normal_font,
                    bold_font,
                )
            )
        if is_comparison and after_state_summary.sections:
            if before_state_summary.sections:
                story.append(Spacer(1, 8 * mm))
            story.extend(
                _make_state_grade_table(
                    "보수후 단위구간 상태등급",
                    after_state_summary,
                    doc.width,
                    normal_font,
                    bold_font,
                )
            )

    doc.build(story, onFirstPage=_draw_page_footer, onLaterPages=_draw_page_footer)
    return str(pdf_path)
