from __future__ import annotations

import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import cv2
from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    QThread,
    QTimer,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QImage,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygon,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sewerpipe_inspector.db import Database, MANHOLE_FIELDS, REPORT_FIELDS
from sewerpipe_inspector.defect_taxonomy import (
    GRADE_ORDER,
    ITEM_CATEGORIES,
    condition_items_for_category,
    defect_definitions_for_category,
    defect_score,
    grades_for_defect,
)
from sewerpipe_inspector.logging_config import configure_logging
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.pdf_report_service import generate_pipe_pdf_report
from sewerpipe_inspector.services.storage_service import StorageService
from sewerpipe_inspector.services.video_service import VideoMeta, VideoService
from sewerpipe_inspector.settings_service import load_settings, save_settings
from sewerpipe_inspector.state_grading import (
    compute_pipe_state_grades,
    format_state_distance,
    format_state_section_label,
    format_state_time_range,
    format_state_value,
)
from sewerpipe_inspector.stop_detection import (
    DepthOcrStopDetectionConfig,
    DepthOcrStopSegmentDetector,
    StopFrameCandidateDetectionConfig,
    StopFrameCandidateDetector,
)
from sewerpipe_inspector.ui.dialogs import (
    BusinessDialog,
    ProjectDialog,
    ReportDialog,
    ReportExportDialog,
)
from sewerpipe_inspector.ui.widgets import TimelineSlider


ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_NAV_TYPE = Qt.ItemDataRole.UserRole + 2
ROLE_VERSION_GROUP_ID = Qt.ItemDataRole.UserRole + 3
ROLE_STOP_ITEM_TYPE = Qt.ItemDataRole.UserRole + 20
ROLE_STOP_TIMESTAMP_MS = Qt.ItemDataRole.UserRole + 21
TABLE_ROW_COLOR = "#ffffff"
TABLE_ALT_ROW_COLOR = "#f6f8fa"
TABLE_LABEL_COLOR = "#e8edf5"
TABLE_TEXT_COLOR = "#111827"
TABLE_GRID_COLOR = "#d7dde8"
VIDEO_FILE_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}
VIDEO_DISPLAY_WIDTH = 604
VIDEO_DISPLAY_HEIGHT = 340
STOP_ANALYSIS_CANDIDATE_WORKERS = 8
STOP_SEGMENT_PANEL_DEFAULT_WIDTH = 137
STOP_SEGMENT_PANEL_MIN_WIDTH = 42
STOP_SEGMENT_PANEL_MAX_WIDTH = 420
STOP_SEGMENT_PANEL_COMPACT_WIDTH = 88
STOP_SEGMENT_PANEL_COLLAPSE_THRESHOLD = 24
STOP_SEGMENT_RESIZE_HANDLE_WIDTH = 6
APP_STYLE = """
QWidget {
    background-color: #f4f6fa;
    color: #111827;
    font-size: 13px;
}
QGroupBox {
    background-color: #ffffff;
    border: 1px solid #e5eaf2;
    border-radius: 0;
    margin-top: 18px;
    padding: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #111827;
    font-weight: 700;
}
QPushButton#workspaceButton {
    background-color: #ffffff;
    border: 1px solid #d7dde8;
    border-radius: 0;
    padding: 7px 12px;
    color: #24324a;
    font-weight: 600;
}
QPushButton#workspaceButton:hover {
    background-color: #f8fafc;
}
QPushButton#sidebarRestoreHandle {
    background-color: #26395f;
    border: 1px solid #496080;
    border-left: none;
    border-radius: 0;
    color: #ffffff;
    font-size: 20px;
    font-weight: 800;
    padding: 0;
}
QPushButton#sidebarRestoreHandle:hover {
    background-color: #1f2f50;
}
#leftPanel {
    background-color: #26395f;
}
QLabel#sidebarBrand {
    background-color: transparent;
    color: #ffffff;
    font-size: 24px;
    font-weight: 800;
}
QLabel#sidebarSubtitle {
    background-color: transparent;
    color: #bfccde;
    font-size: 12px;
}
QGroupBox#navSection {
    background-color: #34486d;
    border: 1px solid #496080;
    border-radius: 0;
    margin-top: 0;
    padding: 0;
}
QGroupBox#navSection::title {
    color: transparent;
    height: 0;
}
QLabel#navSectionTitle {
    background-color: #172948;
    border-bottom: 1px solid #5b7191;
    color: #ffffff;
    font-size: 16px;
    font-weight: 800;
    padding: 10px 12px;
}
QLabel#navSectionTitle[navLevel="project"] {
    background-color: #14284a;
}
QLabel#navSectionTitle[navLevel="business"] {
    background-color: #173a50;
}
QLabel#navSectionTitle[navLevel="report"] {
    background-color: #26315f;
}
QLabel#navSectionTitle[clickable="true"]:hover {
    background-color: #1f3a63;
}
QListWidget#navList {
    background-color: transparent;
    border: none;
    border-radius: 0;
    outline: 0;
    padding: 0;
}
QListWidget#navList::item {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #496080;
    padding: 8px 0;
    margin: 0;
    color: #d9e3f1;
}
QListWidget#navList::item:hover {
    background-color: #40577f;
    color: #ffffff;
}
QListWidget#navList::item:selected {
    background-color: #1f6feb;
    color: #ffffff;
    border-radius: 0;
}
QWidget#rightPanel {
    background-color: #f4f6fa;
}
QWidget#rightPanelContent {
    background-color: #f4f6fa;
}
QWidget#brandBlock {
    background-color: transparent;
}
QWidget#pageShell {
    background-color: #f4f6fa;
}
QLabel#pageTitle {
    background-color: transparent;
    color: #0f172a;
    font-size: 24px;
    font-weight: 800;
}
QLabel#pageSubtitle {
    background-color: transparent;
    color: #6b7280;
}
QLabel#breadcrumb {
    background-color: transparent;
    color: #8b95a5;
}
QWidget#contentCard {
    background-color: transparent;
    border: none;
    border-radius: 0;
}
QLabel#cardMeta {
    background-color: transparent;
    color: #4b5563;
    font-weight: 600;
}
QLabel#sectionTitle {
    background-color: transparent;
    color: #111827;
    font-size: 17px;
    font-weight: 800;
    padding: 6px 0;
}
QWidget#defectSection {
    background-color: transparent;
}
QTableWidget#defectTable {
    border: none;
    border-top: 2px solid #1f2937;
}
QLineEdit, QComboBox {
    background-color: #ffffff;
    border: 1px solid #d7dde8;
    border-radius: 0;
    min-height: 24px;
    padding: 2px 6px;
    color: #111827;
}
QTableWidget QLineEdit, QTableWidget QComboBox {
    min-height: 22px;
    padding: 1px 4px;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #1f6feb;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
}
QCheckBox::indicator:unchecked {
    background-color: #ffffff;
    border: 1px solid #64748b;
}
QSlider#timelineSlider::groove:horizontal {
    height: 8px;
    background-color: #d7dde8;
    border: 1px solid #c5cedc;
    border-radius: 0;
}
QSlider#timelineSlider::sub-page:horizontal {
    background-color: #1f6feb;
    border: 1px solid #1f6feb;
    border-radius: 0;
}
QSlider#timelineSlider::add-page:horizontal {
    background-color: #d7dde8;
    border: 1px solid #c5cedc;
    border-radius: 0;
}
QSlider#timelineSlider::handle:horizontal {
    background-color: #ffffff;
    border: 2px solid #1f6feb;
    width: 14px;
    margin: -5px 0;
    border-radius: 0;
}
QPushButton {
    background-color: #ffffff;
    border: 1px solid #d7dde8;
    border-radius: 0;
    padding: 7px 12px;
    color: #24324a;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #f8fafc;
}
QPushButton:pressed {
    background-color: #eef4ff;
}
QPushButton#tablePickerButton {
    min-height: 28px;
    padding: 2px 6px;
    text-align: center;
}
QTableWidget QPushButton#tablePickerButton {
    min-height: 28px;
    padding: 1px 6px;
}
QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled {
    background-color: #eef2f7;
    color: #8b95a5;
}
QPushButton#primaryButton {
    background-color: #1f6feb;
    border: 1px solid #1f6feb;
    color: #ffffff;
}
QPushButton#primaryButton:hover {
    background-color: #185bc6;
}
QPushButton#primaryButton:disabled {
    background-color: #a7c5f7;
    border-color: #a7c5f7;
    color: #ffffff;
}
QPushButton#secondaryButton {
    background-color: #ffffff;
    border: 1px solid #cfd7e5;
    color: #1f2937;
}
QPushButton#stopAnalyzeButton {
    background-color: #ffffff;
    border: 1px solid #cfd7e5;
    border-radius: 0;
    color: #1f2937;
    font-size: 12px;
    font-weight: 600;
    padding: 2px 5px;
}
QPushButton#stopAnalyzeButton:hover {
    background-color: #f8fafc;
}
QPushButton#stopAnalyzeButton:pressed {
    background-color: #eef4ff;
}
QPushButton#dangerButton {
    background-color: #ffffff;
    border: 1px solid #f1c9c9;
    color: #b42318;
}
QPushButton#dangerButton:hover {
    background-color: #fff5f5;
}
QPushButton#exportButton {
    background-color: #26395f;
    border: 1px solid #26395f;
    color: #ffffff;
}
QPushButton#exportButton:hover {
    background-color: #1f2f50;
}
QTableWidget {
    background-color: #ffffff;
    alternate-background-color: #fbfcfe;
    gridline-color: #cfd7e5;
    border: none;
    border-top: 2px solid #1f2937;
    border-radius: 0;
    color: #111827;
}
QTableWidget::item {
    border-right: 1px solid #d7dde8;
    border-bottom: 1px solid #d7dde8;
    padding: 6px;
}
QTableWidget::item:selected {
    background-color: #e8f1ff;
    color: #111827;
}
QHeaderView::section {
    background-color: #e8edf5;
    border: none;
    border-right: 1px solid #d7dde8;
    border-bottom: 1px solid #e5eaf2;
    padding: 8px;
    color: #1f2937;
    font-weight: 700;
}
QListWidget {
    background-color: #ffffff;
    border: 1px solid #e5eaf2;
    border-radius: 0;
    padding: 6px;
}
QListWidget::item {
    background-color: #ffffff;
    border: 1px solid #e5eaf2;
    border-radius: 0;
    padding: 7px 8px;
    margin: 2px 0;
}
QListWidget::item:hover {
    background-color: #f8fafc;
}
QListWidget::item:selected {
    background-color: #dbeafe;
    border-left: 4px solid #2563eb;
    color: #111827;
}
QListWidget#stopSegmentList {
    padding: 2px;
}
QListWidget#stopSegmentList::item {
    padding: 3px 4px;
    margin: 1px 0;
}
QListWidget#stopSegmentList::item:selected {
    border-left: 2px solid #2563eb;
}
QScrollArea {
    border: none;
    background-color: #f4f6fa;
}
QSplitter::handle {
    background-color: #edf1f6;
}
QLabel#videoLabel {
    background-color: #111111;
    color: #dddddd;
}
QLabel#capturePreviewLabel {
    background-color: #111111;
    color: #dddddd;
}
QLabel#stopSegmentTitle {
    color: #111827;
    font-size: 13px;
    font-weight: 700;
}
QLabel#stopAnalysisStatus {
    color: #475569;
    font-size: 11px;
    padding: 0 8px 2px 8px;
}
QWidget#stopSegmentPanel {
    border-top: 1px solid #cfd7e5;
}
QFrame#stopSegmentResizeHandle {
    background-color: transparent;
    border: none;
    border-left: 1px solid #cfd7e5;
}
QFrame#stopSegmentResizeHandle:hover {
    background-color: #eef4ff;
    border-left: 1px solid #9bbaf3;
}
QFrame#stopSegmentResizeHandle[collapsed="true"] {
    border-left: 1px solid transparent;
}
QWidget#videoOverlay {
    background-color: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 rgba(0, 0, 0, 0),
        stop: 1 rgba(0, 0, 0, 150)
    );
}
QWidget#videoOverlay QLabel {
    background-color: transparent;
    color: #ffffff;
}
QWidget#videoOverlay QPushButton {
    background-color: rgba(0, 0, 0, 0);
    border: 1px solid rgba(255, 255, 255, 0);
    color: #ffffff;
    padding: 0;
}
QWidget#videoOverlay QPushButton:hover {
    background-color: rgba(255, 255, 255, 38);
    border: 1px solid rgba(255, 255, 255, 65);
}
QWidget#videoOverlay QPushButton#playerIconButton {
    background-color: transparent;
    border: 1px solid transparent;
}
QWidget#videoOverlay QPushButton#playerTextButton {
    background-color: transparent;
    border: 1px solid transparent;
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
    padding: 0 4px;
}
QWidget#videoOverlay QLabel#playerTimeLabel {
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
}
QWidget#videoOverlay QSlider#timelineSlider {
    background-color: transparent;
}
QWidget#videoOverlay QSlider#timelineSlider::groove:horizontal {
    height: 4px;
    background-color: rgba(255, 255, 255, 42);
    border: none;
    border-radius: 2px;
}
QWidget#videoOverlay QSlider#timelineSlider::sub-page:horizontal {
    background-color: rgba(255, 255, 255, 230);
    border: none;
    border-radius: 2px;
}
QWidget#videoOverlay QSlider#timelineSlider::add-page:horizontal {
    background-color: rgba(255, 255, 255, 42);
    border: none;
    border-radius: 2px;
}
QWidget#videoOverlay QSlider#timelineSlider::handle:horizontal {
    background-color: #ffffff;
    border: none;
    width: 10px;
    margin: -4px 0;
    border-radius: 5px;
}
"""

REPORT_FIELD_LABELS = [
    ("report_number", "보고서번호"),
    ("pipe_number", "관로번호"),
    ("survey_purpose", "조사목적"),
    ("survey_date", "조사일자"),
    ("buried_years", "매설년수(년)"),
    ("inspector", "조사자"),
    ("contractor", "시공자"),
    ("treatment_area", "처리구역"),
    ("drainage_area", "배수구역"),
    ("drainage_district", "배수분구"),
    ("drain_type", "배수방식"),
    ("drain_system", "배수체계"),
    ("province", "시도"),
    ("city_county", "시군구"),
    ("town", "읍면동"),
    ("village", "리"),
    ("lot_number", "지번"),
    ("road_address", "도로명주소"),
    ("pipe_type", "관종"),
    ("category", "구분"),
    ("specification", "규격"),
]

REPORT_INFO_TABLE_ROWS = [
    [
        ("report_number", "보고서번호*"),
        ("pipe_number", "관로번호*"),
        ("survey_date", "조사일자*"),
    ],
    [
        ("buried_years", "매설년수(년)*"),
        ("survey_purpose", "조사목적"),
        ("inspector", "조사자"),
    ],
    [
        ("contractor", "시공자"),
        ("treatment_area", "처리구역"),
        ("drainage_area", "배수구역"),
    ],
    [
        ("drainage_district", "배수분구"),
        ("drain_type", "배수방식"),
        ("drain_system", "배수체계"),
    ],
    [
        ("province", "시도"),
        ("city_county", "시군구"),
        ("town", "읍면동"),
    ],
    [
        ("village", "리"),
        ("lot_number", "지번"),
        ("road_address", "도로명주소"),
    ],
    [
        ("pipe_type", "관종"),
        ("category", "구분"),
        ("specification", "규격"),
    ],
]

CONTEXT_MERGE_LABELS = [
    ("project_name", "프로젝트명"),
    ("business_code", "사업코드"),
    ("business_name", "사업명"),
    ("client", "발주처"),
    ("business_start_date", "사업시작일"),
    ("business_end_date", "사업완료일"),
]

MANHOLE_LABELS = [
    ("manhole_number", "맨홀번호"),
    ("manhole_type", "맨홀종류"),
    ("internal_material", "맨홀 내부재질"),
    ("cover_material", "맨홀 뚜껑재질"),
    ("el_m", "맨홀 E.L(m)"),
    ("size", "맨홀크기"),
    ("depth_m", "맨홀깊이(m)"),
    ("invert", "맨홀 인버트"),
    ("ladder_shape", "사다리모양"),
    ("latitude", "위도"),
    ("longitude", "경도"),
]

PIPE_MERGE_LABELS = [
    ("length_m", "연장(m)"),
    ("total_drive_distance_m", "총주행거리(m)"),
]

ACTUAL_SURVEY_LABELS = [
    ("start_occurrence_point_m", "시작->끝 발생지점(m)"),
    ("start_undriven_reason", "시작->끝 미주행사유"),
    ("start_undriven_reason_detail", "시작->끝 미주행사유설명"),
    ("end_occurrence_point_m", "끝->시작 발생지점(m)"),
    ("end_undriven_reason", "끝->시작 미주행사유"),
    ("end_undriven_reason_detail", "끝->시작 미주행사유설명"),
    ("survey_content", "조사내용"),
]

MANHOLE_DROPDOWN_OPTIONS = {
    "manhole_type": [
        "",
        "NM(표준 맨홀)",
        "SM(소형 맨홀)",
        "IS(역사이펀실)",
        "AR(터널/박스 암거)",
        "OF(배출구)",
        "CP(우수/오수 받이)",
        "UT(유트랩)",
        "OC(기타 맨홀)",
    ],
    "internal_material": [
        "",
        "AC(시멘트)",
        "AL(알루미늄)",
        "CI(주철강)",
        "CS(탄소강)",
        "SS(스테인리스강)",
        "PVC(폴리염화비닐)",
        "PE(폴리에틸렌)",
        "PS(폴리에스테르)",
        "PP(폴리프로필렌)",
        "PM(강 보강 플라스틱)",
        "RC(철근 콘크리트)",
        "GRP(유리섬유복합)",
        "Z(기타)",
    ],
    "cover_material": ["", "주철", "콘크리트", "스테인레스", "기타", "맨홀없음", "BOX관"],
    "invert": ["", "유", "무"],
    "ladder_shape": [
        "",
        "S(한 다리만 디딜 수 있는 길이)",
        "D(두 다리 모두 디딜 수 있는 길이)",
        "T(발가락을 걸칠 수 있는 홈만 존재)",
        "N(발을 디딜 곳이 없는 경우)",
        "Z(기타)",
    ],
}

UNDROVE_REASONS = [
    "없음",
    "맨홀뚜껑파손",
    "맨홀연결파손",
    "연결관돌출",
    "연결관접합부",
    "이음부",
    "침입수",
    "유출수",
    "부식",
    "관파속및크랙",
    "곡관로",
    "관침하",
    "관구배",
    "타관통과",
    "폐유",
    "모르타르",
    "토사퇴적",
    "개폐불가",
    "맨홀묻힘",
    "기타",
]

QUADRANTS = [
    "없음",
    "상",
    "상우하",
    "우",
    "우상",
    "우상좌하",
    "우하",
    "좌",
    "좌상",
    "좌상우하",
    "좌하",
    "하",
    "하우상",
    "하좌상",
    "전체",
]


def format_timestamp(timestamp_ms: int) -> str:
    milliseconds = timestamp_ms % 1000
    seconds = timestamp_ms // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def format_short_timestamp(timestamp_ms: int) -> str:
    seconds = timestamp_ms // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def analyze_stop_frame_candidate_segment(
    video_path: str,
    segment: dict[str, float],
) -> dict[str, object]:
    candidate_detector = StopFrameCandidateDetector(
        StopFrameCandidateDetectionConfig(
            fps=5.0,
            max_candidates_per_segment=3,
            min_stable_duration=0.6,
            min_candidate_gap=1.2,
        )
    )
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")
    try:
        read_started = time.perf_counter()
        samples = candidate_detector._read_segment_samples(cap, segment)
        read_seconds = time.perf_counter() - read_started
    finally:
        cap.release()

    select_started = time.perf_counter()
    candidates = candidate_detector._select_candidates(samples, segment)
    select_seconds = time.perf_counter() - select_started

    output = dict(segment)
    output["candidates"] = candidates
    output["analysis_seconds"] = read_seconds + select_seconds
    output["candidate_read_seconds"] = read_seconds
    output["candidate_select_seconds"] = select_seconds
    output["candidate_sample_count"] = len(samples)
    return output


class StopAnalysisWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object, object)
    failed = Signal(str)

    def __init__(
        self,
        video_path: str,
        depth_roi: tuple[int, int, int, int],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.video_path = video_path
        self.depth_roi = depth_roi

    def run(self) -> None:
        total_started = time.perf_counter()
        benchmark: dict[str, object] = {
            "depth_seconds": 0.0,
            "candidate_seconds": 0.0,
            "total_seconds": 0.0,
            "candidate_error": None,
        }

        def depth_progress(current: int, total: int, message: str) -> None:
            value = int(max(0, min(45, (current / max(1, total)) * 45)))
            self.progress.emit(value, message)

        try:
            self.progress.emit(0, "거리 OCR 준비")
            depth_detector = DepthOcrStopSegmentDetector(
                DepthOcrStopDetectionConfig(
                    fps=1.0,
                    min_stop_duration=3.0,
                    stop_distance_tolerance_m=0.5,
                    merge_gap_threshold=1.0,
                    max_missing_bridge_seconds=3.0,
                    min_digit_score=0.5,
                )
            )
            started = time.perf_counter()
            stop_segments = depth_detector.analyze(
                self.video_path,
                self.depth_roi,
                progress_callback=depth_progress,
            )
            benchmark["depth_seconds"] = time.perf_counter() - started
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        candidate_started = time.perf_counter()
        analyzed_segments: list[dict[str, object]] = []
        candidate_error: str | None = None
        try:
            total_segments = len(stop_segments)
            self.progress.emit(
                45,
                "후보 프레임 병렬 분석 준비"
                f" · 의심구간 {total_segments}개"
                f" · 워커 {STOP_ANALYSIS_CANDIDATE_WORKERS}개",
            )
            if total_segments == 0:
                analyzed_segments = []
            else:
                completed = 0
                indexed_results: dict[int, dict[str, object]] = {}
                max_workers = max(1, min(STOP_ANALYSIS_CANDIDATE_WORKERS, total_segments))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_index = {
                        executor.submit(
                            analyze_stop_frame_candidate_segment,
                            self.video_path,
                            segment,
                        ): idx
                        for idx, segment in enumerate(stop_segments)
                    }
                    for future in as_completed(future_to_index):
                        idx = future_to_index[future]
                        result = future.result()
                        indexed_results[idx] = result
                        completed += 1
                        self.progress.emit(
                            45 + int((completed / total_segments) * 55),
                            "후보 프레임 병렬 분석"
                            f" {completed}/{total_segments} 완료"
                            f" · 최근 {float(result.get('analysis_seconds', 0.0)):.2f}s",
                        )
                analyzed_segments = [
                    indexed_results[idx] for idx in range(total_segments)
                ]
                benchmark["candidate_workers"] = max_workers
                benchmark["candidate_segment_seconds_sum"] = sum(
                    float(segment.get("analysis_seconds", 0.0))
                    for segment in analyzed_segments
                )
                benchmark["candidate_slowest_seconds"] = max(
                    (
                        float(segment.get("analysis_seconds", 0.0))
                        for segment in analyzed_segments
                    ),
                    default=0.0,
                )
                benchmark["candidate_fastest_seconds"] = min(
                    (
                        float(segment.get("analysis_seconds", 0.0))
                        for segment in analyzed_segments
                    ),
                    default=0.0,
                )
        except Exception as exc:
            candidate_error = str(exc)
            analyzed_segments = [
                dict(segment, candidates=[], analysis_error=candidate_error)
                for segment in stop_segments
            ]

        benchmark["candidate_seconds"] = time.perf_counter() - candidate_started
        benchmark["candidate_error"] = candidate_error
        benchmark["total_seconds"] = time.perf_counter() - total_started
        self.progress.emit(100, "의심구간 분석 완료")
        self.finished.emit(analyzed_segments, benchmark)


def make_player_icon(name: str, size: int = 28) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 28, size / 28)
    white = QColor("#ffffff")
    pen = QPen(
        white,
        2.3,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    painter.setPen(pen)
    painter.setBrush(white)

    if name == "play":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(0, 6), QPoint(20, 14), QPoint(0, 22)]))
    elif name == "pause":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(9, 7, 3.8, 14))
        painter.drawRect(QRectF(15.2, 7, 3.8, 14))
    elif name == "frame-back":
        painter.drawLine(QPointF(8, 7), QPointF(8, 21))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(20, 7), QPoint(10, 14), QPoint(20, 21)]))
    elif name == "frame-forward":
        painter.drawLine(QPointF(20, 7), QPointF(20, 21))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(8, 7), QPoint(18, 14), QPoint(8, 21)]))
    elif name == "seek-back":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(15, 7), QPoint(7, 14), QPoint(15, 21)]))
        painter.drawPolygon(QPolygon([QPoint(23, 7), QPoint(15, 14), QPoint(23, 21)]))
    elif name == "seek-forward":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygon([QPoint(5, 7), QPoint(13, 14), QPoint(5, 21)]))
        painter.drawPolygon(QPolygon([QPoint(13, 7), QPoint(21, 14), QPoint(13, 21)]))
    elif name == "volume":
        painter.drawPolygon(
            QPolygon(
                [
                    QPoint(6, 12),
                    QPoint(10, 12),
                    QPoint(16, 7),
                    QPoint(16, 21),
                    QPoint(10, 16),
                    QPoint(6, 16),
                ]
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(14, 9, 7, 10), -45 * 16, 90 * 16)
        painter.drawArc(QRectF(13, 6, 11, 16), -45 * 16, 90 * 16)
    elif name == "camera":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(6, 10, 16, 11))
        painter.drawRect(QRectF(10, 7, 8, 4))
        painter.drawEllipse(QPointF(14, 15.5), 3.2, 3.2)
    elif name == "fullscreen":
        painter.drawLine(QPointF(8, 12), QPointF(8, 8))
        painter.drawLine(QPointF(8, 8), QPointF(12, 8))
        painter.drawLine(QPointF(16, 8), QPointF(20, 8))
        painter.drawLine(QPointF(20, 8), QPointF(20, 12))
        painter.drawLine(QPointF(20, 16), QPointF(20, 20))
        painter.drawLine(QPointF(20, 20), QPointF(16, 20))
        painter.drawLine(QPointF(12, 20), QPointF(8, 20))
        painter.drawLine(QPointF(8, 20), QPointF(8, 16))

    painter.end()
    return QIcon(pixmap)


def parse_float(text: str) -> float | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError("숫자 필드 값을 확인하세요") from exc


def parse_required_float(text: str, label: str) -> float:
    value = parse_float(text)
    if value is None:
        raise ValueError(f"{label}를 입력하세요")
    return value


def set_combo_text(combo: QComboBox, value: object, default: str | None = None) -> None:
    target = str(value) if value not in (None, "") else default
    if target is None:
        if combo.count() > 0:
            combo.setCurrentIndex(0)
        return
    idx = combo.findText(target)
    if idx < 0 and default is not None:
        idx = combo.findText(default)
    if idx < 0 and combo.count() > 0:
        idx = 0
    if idx >= 0:
        combo.setCurrentIndex(idx)


def input_widget_text(widget: QWidget) -> str:
    if isinstance(widget, QComboBox):
        return widget.currentText().strip()
    if isinstance(widget, QLineEdit):
        return widget.text().strip()
    return ""


def set_input_widget_text(widget: QWidget, value: object) -> None:
    if isinstance(widget, QComboBox):
        set_combo_text(widget, value, "")
        return
    if isinstance(widget, QLineEdit):
        widget.setText("" if value is None else str(value))


def clear_input_widget(widget: QWidget) -> None:
    if isinstance(widget, QComboBox):
        widget.setCurrentIndex(0)
        return
    if isinstance(widget, QLineEdit):
        widget.clear()


def read_only_table_item(
    text: object = "", *, is_label: bool = False, row_index: int | None = None
) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    item.setForeground(QColor(TABLE_TEXT_COLOR))
    if is_label:
        item.setBackground(QColor(TABLE_LABEL_COLOR))
    elif row_index is not None:
        item.setBackground(
            QColor(TABLE_ROW_COLOR if row_index % 2 == 0 else TABLE_ALT_ROW_COLOR)
        )
    return item


class TableBackgroundDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        background = index.data(Qt.ItemDataRole.BackgroundRole)
        if background is None:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.save()
        if opt.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(opt.rect, QColor("#e8f1ff"))
        else:
            painter.fillRect(opt.rect, background)
        painter.setPen(QColor(TABLE_TEXT_COLOR))
        painter.drawText(opt.rect.adjusted(6, 0, -6, 0), opt.displayAlignment, opt.text)
        painter.setPen(QColor(TABLE_GRID_COLOR))
        painter.drawLine(opt.rect.topRight(), opt.rect.bottomRight())
        painter.drawLine(opt.rect.bottomLeft(), opt.rect.bottomRight())
        painter.restore()


class SidebarRestoreHandle(QPushButton):
    dragged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(">", parent)
        self.setObjectName("sidebarRestoreHandle")
        self.setFixedSize(22, 64)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("사이드바 열기")
        self._press_x: int | None = None
        self._dragged = False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_x = int(event.globalPosition().x())
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_x is None:
            super().mouseMoveEvent(event)
            return
        delta = int(event.globalPosition().x()) - self._press_x
        if delta > 4:
            self._dragged = True
            self.dragged.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_x = None
        if self._dragged:
            self._dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class StopSegmentResizeHandle(QFrame):
    drag_started = Signal()
    dragged = Signal(int)
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("stopSegmentResizeHandle")
        self.setFixedSize(STOP_SEGMENT_RESIZE_HANDLE_WIDTH, VIDEO_DISPLAY_HEIGHT)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setProperty("collapsed", "false")
        self._press_x: int | None = None
        self._dragged = False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_x = int(event.globalPosition().x())
            self._dragged = False
            self.drag_started.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_x is None:
            super().mouseMoveEvent(event)
            return
        delta = int(event.globalPosition().x()) - self._press_x
        if abs(delta) > 1:
            self._dragged = True
            self.dragged.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._press_x is not None:
            self._press_x = None
            if not self._dragged and event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit()
            self._dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PopupTablePickerButton(QPushButton):
    currentTextChanged = Signal(str)

    def __init__(self, placeholder: str, parent=None) -> None:
        super().__init__(placeholder, parent)
        self.setObjectName("tablePickerButton")
        self._placeholder = placeholder
        self._current_text = ""
        self._headers: list[str] = []
        self._rows: list[list[object]] = []
        self._grid_columns = 0
        self._allow_empty = False
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._show_popup)

    def currentText(self) -> str:
        return self._current_text

    def set_allow_empty(self, allow_empty: bool) -> None:
        self._allow_empty = allow_empty

    def setCurrentText(self, value: object, default: str | None = None) -> None:
        target = str(value) if value not in (None, "") else default
        values = self._values()
        if self._allow_empty and value in (None, ""):
            target = ""
        if target not in values:
            target = default if default in values else values[0] if values else ""
        if target == self._current_text:
            self._update_button_text()
            return
        self._current_text = target
        self._update_button_text()
        self.currentTextChanged.emit(self._current_text)

    def set_grid_items(self, items: list[str], columns: int = 3) -> None:
        self._headers = []
        self._rows = ([[""]] if self._allow_empty else []) + [[item] for item in items]
        self._grid_columns = columns
        self.setCurrentText(
            self._current_text,
            "" if self._allow_empty else items[0] if items else None,
        )

    def set_table_items(self, headers: list[str], rows: list[list[object]]) -> None:
        self._headers = headers
        empty_row = [[""] + [""] * (max(1, len(headers)) - 1)] if self._allow_empty else []
        self._rows = empty_row + rows
        self._grid_columns = 0
        default = str(rows[0][0]) if rows else None
        self.setCurrentText(self._current_text, "" if self._allow_empty else default)

    def _values(self) -> list[str]:
        return [str(row[0]) for row in self._rows if row]

    def _update_button_text(self) -> None:
        self.setText(self._current_text or self._placeholder)

    def _show_popup(self) -> None:
        if not self._rows:
            return
        popup = QFrame(self, Qt.WindowType.Popup)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(0, 0, 0, 0)
        table = QTableWidget(popup)
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setWordWrap(True)
        table.setShowGrid(True)
        if self._grid_columns:
            self._populate_grid_popup(table)
        else:
            self._populate_table_popup(table)
        table.cellPressed.connect(
            lambda row, col, table=table, popup=popup: self._select_popup_value(
                table, popup, row, col
            )
        )
        layout.addWidget(table)
        popup_pos, popup_size = self._bounded_popup_geometry(table.size())
        table.setFixedSize(popup_size)
        popup.setFixedSize(popup_size)
        popup.move(popup_pos)
        popup.show()

    def _bounded_popup_geometry(self, desired_size: QSize) -> tuple[QPoint, QSize]:
        margin = 8
        anchor_top = self.mapToGlobal(QPoint(0, 0))
        anchor_bottom = self.mapToGlobal(QPoint(0, self.height()))
        screen = QApplication.screenAt(anchor_bottom)
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        if available is None:
            return anchor_bottom, desired_size

        available_left = available.x() + margin
        available_top = available.y() + margin
        available_right = available.x() + available.width() - margin
        available_bottom = available.y() + available.height() - margin
        max_width = max(160, available_right - available_left)
        max_height = max(120, available_bottom - available_top)

        width = min(desired_size.width(), max_width)
        height = min(desired_size.height(), max_height)
        below_space = max(0, available_bottom - anchor_bottom.y())
        above_space = max(0, anchor_top.y() - available_top)

        if height > below_space and above_space > below_space:
            height = min(height, max(120, above_space))
            y = anchor_top.y() - height
        else:
            height = min(height, max(120, below_space))
            y = anchor_bottom.y()

        x = anchor_bottom.x()
        if x + width > available_right:
            x = available_right - width
        x = max(available_left, x)
        y = max(available_top, min(y, available_bottom - height))
        return QPoint(x, y), QSize(width, height)

    def _populate_grid_popup(self, table: QTableWidget) -> None:
        columns = max(1, self._grid_columns)
        row_count = (len(self._rows) + columns - 1) // columns
        table.setRowCount(row_count)
        table.setColumnCount(columns)
        table.horizontalHeader().hide()
        for col in range(columns):
            table.setColumnWidth(col, 210)
        for row in range(row_count):
            table.setRowHeight(row, 62)
        for idx, row_values in enumerate(self._rows):
            row = idx // columns
            col = idx % columns
            display_value = str(row_values[0]) if row_values[0] not in (None, "") else "(빈칸)"
            item = read_only_table_item(display_value)
            item.setData(Qt.ItemDataRole.UserRole, str(row_values[0]))
            table.setItem(row, col, item)
            if str(row_values[0]) == self._current_text:
                table.setCurrentCell(row, col)
        content_height = row_count * 62 + table.frameWidth() * 2
        height = min(430, content_height)
        width = table.horizontalHeader().length() + table.frameWidth() * 2
        if content_height > height:
            width += table.style().pixelMetric(
                QStyle.PixelMetric.PM_ScrollBarExtent, None, table
            )
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setFixedSize(width, height)

    def _populate_table_popup(self, table: QTableWidget) -> None:
        table.setRowCount(len(self._rows))
        table.setColumnCount(len(self._headers))
        table.setHorizontalHeaderLabels(self._headers)
        configure_table_headers(table)
        widths = [210, 76, 64, 64, 64]
        for col, width in enumerate(widths[: len(self._headers)]):
            table.setColumnWidth(col, width)
        row_height = 48
        for row, row_values in enumerate(self._rows):
            table.setRowHeight(row, row_height)
            for col, value in enumerate(row_values):
                display_value = "(빈칸)" if col == 0 and value in (None, "") else value
                item = read_only_table_item(display_value)
                item.setData(Qt.ItemDataRole.UserRole, str(row_values[0]))
                table.setItem(row, col, item)
            if str(row_values[0]) == self._current_text:
                table.setCurrentCell(row, 0)
        content_height = (
            table.horizontalHeader().height()
            + len(self._rows) * row_height
            + table.frameWidth() * 2
        )
        height = min(520, content_height)
        width = table.horizontalHeader().length() + table.frameWidth() * 2
        if content_height > height:
            width += table.style().pixelMetric(
                QStyle.PixelMetric.PM_ScrollBarExtent, None, table
            )
        table.setFixedSize(width, height)

    def _select_popup_value(
        self, table: QTableWidget, popup: QFrame, row: int, col: int
    ) -> None:
        item = table.item(row, col)
        if item is None:
            return
        value = item.data(Qt.ItemDataRole.UserRole)
        if value is None:
            return
        self.setCurrentText(value)
        popup.close()


def configure_table_headers(table: QTableWidget) -> None:
    for col in range(table.columnCount()):
        item = table.horizontalHeaderItem(col)
        if item is not None:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)


def configure_table_rows(table: QTableWidget, row_height: int = 38) -> None:
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setWordWrap(False)
    table.setShowGrid(True)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setItemDelegate(TableBackgroundDelegate(table))


def fit_table_height_to_contents(table: QTableWidget) -> None:
    header_height = 0
    if not table.horizontalHeader().isHidden():
        header_height = table.horizontalHeader().height()
    rows_height = sum(table.rowHeight(row) for row in range(table.rowCount()))
    table.setFixedHeight(header_height + rows_height + 2)


def apply_column_widths(
    table: QTableWidget,
    widths: dict[int, int],
    stretch_columns: set[int] | None = None,
) -> None:
    stretch_columns = stretch_columns or set()
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(46)
    for col in range(table.columnCount()):
        table.setColumnWidth(col, widths.get(col, 100))
        if col in stretch_columns:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)


def configure_line_edit(edit: QLineEdit, width: int | None = None) -> None:
    edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
    edit.setMinimumHeight(24)
    edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    if width is not None:
        edit.setMinimumWidth(width)


def configure_combo(combo: QComboBox, width: int | None = None) -> None:
    combo.setEditable(False)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setMinimumHeight(24)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    combo.view().setTextElideMode(Qt.TextElideMode.ElideNone)
    combo.model().rowsInserted.connect(
        lambda *_args, combo=combo: update_combo_popup_width(combo)
    )
    if width is not None:
        combo.setMinimumWidth(width)
    QTimer.singleShot(0, lambda combo=combo: update_combo_popup_width(combo))


def update_combo_popup_width(combo: QComboBox) -> None:
    longest = 0
    metrics = combo.view().fontMetrics()
    for idx in range(combo.count()):
        longest = max(longest, metrics.horizontalAdvance(combo.itemText(idx)))
    indicator_width = combo.style().pixelMetric(
        QStyle.PixelMetric.PM_IndicatorWidth, None, combo
    )
    scrollbar_width = combo.style().pixelMetric(
        QStyle.PixelMetric.PM_ScrollBarExtent, None, combo
    )
    padding = indicator_width + scrollbar_width + 44
    combo.view().setMinimumWidth(max(combo.width(), longest + padding))


def allow_cell_widget_shrink(widget: QWidget) -> None:
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)


class StableImageLabel(QLabel):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._source_pixmap: QPixmap | None = None

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = QPixmap(pixmap)
        super().setText("")
        self.update()

    def clear(self) -> None:
        self._source_pixmap = None
        super().clear()
        self.update()

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSizeHint(self) -> QSize:
        return self.minimumSize()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111111"))

        if self._source_pixmap is None or self._source_pixmap.isNull():
            painter.setPen(QColor("#dddddd"))
            painter.drawText(self.rect(), self.alignment(), self.text())
            painter.end()
            return

        target_size = self._source_pixmap.size()
        target_size.scale(self.rect().size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - target_size.width()) // 2
        y = (self.height() - target_size.height()) // 2
        painter.drawPixmap(
            x,
            y,
            target_size.width(),
            target_size.height(),
            self._source_pixmap,
        )
        painter.end()


class MainWindow(QMainWindow):
    def __init__(
        self, db: Database, inspection: InspectionService, parent=None
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.inspection = inspection
        self.logger = logging.getLogger(self.__class__.__name__)

        self.current_project_id: Optional[int] = None
        self.current_business_id: Optional[int] = None
        self.current_report_id: Optional[int] = None
        self.current_version_group_id: Optional[int] = None
        self.current_video_id: Optional[int] = None
        self.current_video_path: Optional[Path] = None
        self.current_video_meta: Optional[VideoMeta] = None
        self.current_frame = None
        self.current_frame_index = 0
        self.current_depth_roi: Optional[tuple[int, int, int, int]] = None
        self.pending_depth_roi: Optional[tuple[int, int, int, int]] = None
        self.is_selecting_depth_roi = False
        self.depth_drag_start: Optional[QPoint] = None
        self.depth_drag_end: Optional[QPoint] = None
        self.pending_capture_frame = None
        self.pending_capture_timestamp_ms: Optional[int] = None
        self.capture_preview_source_pixmap: Optional[QPixmap] = None
        self.editing_defect_id: Optional[int] = None
        self.cap = None
        self.playback_speed = 1.0
        self.is_playing = False
        self.is_user_seeking = False
        self.stop_segments: list[dict[str, float]] = []
        self.stop_analysis_benchmark: dict[str, object] = {}
        self.stop_analysis_thread: QThread | None = None
        self.stop_analysis_worker: StopAnalysisWorker | None = None
        self.stop_segment_panel_width = STOP_SEGMENT_PANEL_DEFAULT_WIDTH
        self._stop_segment_resize_start_width = STOP_SEGMENT_PANEL_DEFAULT_WIDTH
        self._updating_navigation = False
        self._loading_report = False
        self._report_details_dirty = False
        self._expanded_report_version_groups: set[int] = set()
        self._syncing_defect_item_state = False
        self._database_error_reported = False

        self.report_inputs: dict[str, QLineEdit] = {}
        self.manhole_inputs: dict[str, dict[str, QWidget]] = {
            "upstream": {},
            "downstream": {},
        }
        self.report_controls: list[QWidget] = []
        self.video_drop_targets: list[QWidget] = []

        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._play_tick)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self._autosave_report_details)

        self.setWindowTitle("하수관로 점검 데스크톱")
        self.resize(1600, 950)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._register_shortcuts()
        self.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.refresh_tree()
        self._set_report_controls_enabled(False)

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter)

        self.left_panel = self._build_left_panel()
        self.center_panel = self._build_center_panel()
        self.right_panel = self._build_right_panel()
        self.left_panel.setMinimumWidth(250)
        self.left_panel.setMaximumWidth(340)
        self.main_splitter.addWidget(self.left_panel)
        self.main_splitter.addWidget(self.right_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setCollapsible(0, True)
        self.main_splitter.setSizes([292, 1308])
        self.main_splitter.splitterMoved.connect(
            self._update_sidebar_restore_handle
        )

        self.sidebar_restore_handle = SidebarRestoreHandle(root)
        self.sidebar_restore_handle.clicked.connect(self._restore_sidebar)
        self.sidebar_restore_handle.dragged.connect(self._drag_restore_sidebar)
        self.sidebar_restore_handle.hide()
        QTimer.singleShot(0, self._update_sidebar_restore_handle)

    def _sidebar_width(self) -> int:
        sizes = self.main_splitter.sizes()
        if sizes:
            return sizes[0]
        return self.left_panel.width()

    def _update_sidebar_restore_handle(self, *_args) -> None:
        if not hasattr(self, "sidebar_restore_handle"):
            return
        collapsed = self._sidebar_width() <= 24
        self.sidebar_restore_handle.setVisible(collapsed)
        if not collapsed:
            return
        root = self.centralWidget()
        if root is None:
            return
        y = max(0, (root.height() - self.sidebar_restore_handle.height()) // 2)
        self.sidebar_restore_handle.move(0, y)
        self.sidebar_restore_handle.raise_()

    def _set_sidebar_width(self, width: int) -> None:
        total = max(sum(self.main_splitter.sizes()), self.main_splitter.width())
        target = max(250, min(340, width))
        if total > 0:
            target = min(target, max(0, total - 240))
        self.main_splitter.setSizes([target, max(1, total - target)])
        self._update_sidebar_restore_handle()

    def _restore_sidebar(self) -> None:
        self._set_sidebar_width(292)

    def _drag_restore_sidebar(self, delta: int) -> None:
        self._set_sidebar_width(delta)

    def _build_left_panel(self) -> QWidget:
        panel = LeftNavigationPanel(self)
        layout = panel.content_layout
        brand = QWidget(self)
        brand.setObjectName("brandBlock")
        brand.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 6)
        brand_layout.setSpacing(2)
        brand_title = QLabel("Pipe1", self)
        brand_title.setObjectName("sidebarBrand")
        brand_subtitle = QLabel("하수관로 맨홀 조사", self)
        brand_subtitle.setObjectName("sidebarSubtitle")
        brand_layout.addWidget(brand_title)
        brand_layout.addWidget(brand_subtitle)
        layout.addWidget(brand)

        self.project_nav = NavigationList("project", self)
        self.business_nav = NavigationList("business", self)
        self.report_nav = NavigationList("report", self)
        self.report_nav.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_nav.itemSelectionChanged.connect(self._project_selection_changed)
        self.business_nav.itemSelectionChanged.connect(self._business_selection_changed)
        self.report_nav.itemSelectionChanged.connect(self._report_selection_changed)
        self.project_nav.itemClicked.connect(lambda _item: self._project_selection_changed())
        self.business_nav.itemClicked.connect(lambda _item: self._business_selection_changed())
        self.report_nav.itemClicked.connect(lambda _item: self._report_selection_changed())
        self.report_nav.customContextMenuRequested.connect(
            self._show_report_nav_context_menu
        )
        self.project_nav_group = self._wrap_nav_group("프로젝트", self.project_nav)
        self.project_nav_group.set_title_clickable(True)
        self.project_nav_group.title_clicked.connect(self._show_project_root_from_nav)
        self.business_nav_group = self._wrap_nav_group("사업", self.business_nav)
        self.business_nav_group.title_clicked.connect(self._show_business_root_from_nav)
        self.report_nav_group = self._wrap_nav_group("보고서", self.report_nav)
        self.report_nav_group.title_clicked.connect(self._show_report_root_from_nav)
        self._update_nav_title_clickable_states()
        layout.addWidget(self.project_nav_group, 1)
        layout.addWidget(self.business_nav_group, 1)
        layout.addWidget(self.report_nav_group, 1)
        self.workspace_button = QPushButton("작업 폴더 변경", self)
        self.workspace_button.setObjectName("workspaceButton")
        self.workspace_button.clicked.connect(self.change_workspace_directory)
        layout.addWidget(self.workspace_button)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)

        self.video_status_label = QLabel("보고서를 선택하세요")
        self.video_status_label.hide()
        self.video_direction_combo = QComboBox(self)
        self.video_direction_combo.addItems(["순주행", "역주행"])
        configure_combo(self.video_direction_combo)
        self.video_direction_combo.hide()
        self.video_direction_combo.currentTextChanged.connect(self.save_video_info)
        self.add_video_button = QPushButton("영상 등록/교체")
        self.add_video_button.clicked.connect(self.add_or_replace_video)
        self.set_depth_roi_button = QPushButton("깊이 영역 설정")
        self.set_depth_roi_button.clicked.connect(self.set_depth_area)
        self.save_depth_roi_button = QPushButton("깊이 영역 저장")
        self.save_depth_roi_button.setVisible(False)
        self.save_depth_roi_button.setEnabled(False)
        self.save_depth_roi_button.clicked.connect(self.save_depth_area)
        self.detect_stop_button = QPushButton("분석")
        self.detect_stop_button.setObjectName("stopAnalyzeButton")
        self.detect_stop_button.setFixedHeight(28)
        self.detect_stop_button.clicked.connect(self.detect_stop_segments)
        self.show_stop_only_checkbox = QCheckBox("")
        self.show_stop_only_checkbox.hide()
        self.show_stop_only_checkbox.stateChanged.connect(self.refresh_defects)
        self.report_controls.extend(
            [
                self.video_direction_combo,
                self.add_video_button,
                self.set_depth_roi_button,
                self.save_depth_roi_button,
                self.detect_stop_button,
                self.show_stop_only_checkbox,
            ]
        )

        self.video_label = StableImageLabel("", self)
        self.video_label.setObjectName("videoLabel")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.video_label.setFixedSize(VIDEO_DISPLAY_WIDTH, VIDEO_DISPLAY_HEIGHT)
        self.video_label.setMouseTracking(True)
        self._install_video_drop_target(self.video_label)

        video_header = QWidget(self)
        video_header_layout = QHBoxLayout(video_header)
        video_header_layout.setContentsMargins(0, 0, 0, 0)
        video_header_layout.setSpacing(6)
        video_header_layout.addWidget(self._build_section_title("• 영상"))
        video_header_layout.addStretch(1)

        video_header_layout.addWidget(self.add_video_button)
        video_header_layout.addWidget(self.set_depth_roi_button)
        video_header_layout.addWidget(self.save_depth_roi_button)

        self.stop_segment_panel = self._build_stop_segment_panel()

        video_area = QWidget(self)
        video_area_layout = QGridLayout(video_area)
        video_area_layout.setContentsMargins(0, 0, 0, 0)
        video_area_layout.setHorizontalSpacing(0)
        video_area_layout.setVerticalSpacing(8)
        video_area_layout.addWidget(video_header, 0, 0)
        video_area_layout.addWidget(self.video_label, 1, 0)
        video_area_layout.addWidget(self.stop_segment_panel, 1, 1)
        self.stop_segment_resize_handle = StopSegmentResizeHandle(self)
        self.stop_segment_resize_handle.drag_started.connect(
            self._start_stop_segment_panel_resize
        )
        self.stop_segment_resize_handle.dragged.connect(
            self._resize_stop_segment_panel
        )
        self.stop_segment_resize_handle.clicked.connect(
            self._restore_stop_segment_panel
        )
        video_area_layout.addWidget(self.stop_segment_resize_handle, 1, 2)
        video_area_layout.setColumnStretch(0, 0)
        video_area_layout.setColumnStretch(1, 0)
        video_area_layout.setColumnStretch(2, 0)
        video_area_layout.setRowStretch(1, 1)
        layout.addWidget(video_area, 0, 0, 2, 1)

        capture_header = QWidget(self)
        capture_header_layout = QHBoxLayout(capture_header)
        capture_header_layout.setContentsMargins(0, 0, 0, 0)
        capture_header_layout.addWidget(self._build_section_title("• 캡쳐 화면"))
        capture_header_layout.addStretch(1)
        layout.addWidget(capture_header, 0, 1)

        self.capture_preview_label = StableImageLabel("캡처된 프레임 없음", self)
        self.capture_preview_label.setObjectName("capturePreviewLabel")
        self.capture_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.capture_preview_label.setFixedSize(
            VIDEO_DISPLAY_WIDTH, VIDEO_DISPLAY_HEIGHT
        )
        layout.addWidget(self.capture_preview_label, 1, 1)

        self.timeline_slider = TimelineSlider(Qt.Orientation.Horizontal, self)
        self.timeline_slider.setObjectName("timelineSlider")
        self.timeline_slider.setMinimum(0)
        self.timeline_slider.setFixedHeight(18)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._seek_from_slider)
        self.timeline_slider.markerClicked.connect(self._seek_to_marker)
        self.report_controls.append(self.timeline_slider)

        self.video_overlay = QWidget(self.video_label)
        self.video_overlay.setObjectName("videoOverlay")
        self.video_overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._install_video_drop_target(self.video_overlay)
        overlay_layout = QVBoxLayout(self.video_overlay)
        overlay_layout.setContentsMargins(28, 7, 28, 8)
        overlay_layout.setSpacing(2)

        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.setSpacing(8)
        self.play_button = self._make_player_icon_button(
            "play", "재생/일시정지", self.toggle_play
        )
        self.step_back_button = self._make_player_icon_button(
            "frame-back", "이전 프레임", lambda: self.step_frame(-1)
        )
        self.step_forward_button = self._make_player_icon_button(
            "frame-forward", "다음 프레임", lambda: self.step_frame(1)
        )
        self.volume_button = self._make_player_icon_button(
            "volume", "오디오 제어는 지원하지 않습니다."
        )
        self.speed_button = QPushButton("1.0X", self)
        self.speed_button.setObjectName("playerTextButton")
        self.speed_button.setFixedSize(46, 28)
        self.speed_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speed_button.setToolTip("재생 속도 변경")
        self.speed_button.clicked.connect(self._cycle_speed)
        self.capture_overlay_button = self._make_player_icon_button(
            "camera", "현재 프레임 캡처", self.capture_frame
        )
        self.fullscreen_button = self._make_player_icon_button(
            "fullscreen", "전체 화면 전환", self._toggle_window_fullscreen
        )
        self.current_time_label = QLabel("00:00", self)
        self.time_separator_label = QLabel("/", self)
        self.total_time_label = QLabel("00:00", self)
        time_label_width = (
            self.current_time_label.fontMetrics().horizontalAdvance("88:88:88") + 4
        )
        for label in (
            self.current_time_label,
            self.time_separator_label,
            self.total_time_label,
        ):
            label.setObjectName("playerTimeLabel")
        for label in (self.current_time_label, self.total_time_label):
            label.setFixedWidth(time_label_width)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_separator_label.setFixedWidth(10)
        self.time_separator_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for widget in (
            self.play_button,
            self.step_back_button,
            self.step_forward_button,
        ):
            ctrl.addWidget(widget)
            self.report_controls.append(widget)
        ctrl.addSpacing(2)
        ctrl.addWidget(self.current_time_label)
        ctrl.addWidget(self.time_separator_label)
        ctrl.addWidget(self.total_time_label)
        ctrl.addStretch(1)
        ctrl.addWidget(self.volume_button)
        ctrl.addWidget(self.speed_button)
        ctrl.addWidget(self.capture_overlay_button)
        ctrl.addWidget(self.fullscreen_button)
        self.report_controls.extend(
            [
                self.speed_button,
                self.capture_overlay_button,
                self.fullscreen_button,
            ]
        )
        for widget in (
            self.play_button,
            self.step_back_button,
            self.step_forward_button,
            self.volume_button,
            self.speed_button,
            self.capture_overlay_button,
            self.fullscreen_button,
            self.timeline_slider,
        ):
            self._install_video_drop_target(widget)
        overlay_layout.addLayout(ctrl)
        timeline_row = QHBoxLayout()
        timeline_row.setContentsMargins(2, 0, 0, 0)
        timeline_row.setSpacing(0)
        timeline_row.addWidget(self.timeline_slider)
        overlay_layout.addLayout(timeline_row)
        self._position_video_overlay()
        self.video_overlay.show()
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(1, 1)
        return panel

    def _install_video_drop_target(self, widget: QWidget) -> None:
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)
        self.video_drop_targets.append(widget)

    def _build_stop_segment_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setObjectName("stopSegmentPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setFixedWidth(STOP_SEGMENT_PANEL_DEFAULT_WIDTH)
        panel.setFixedHeight(VIDEO_DISPLAY_HEIGHT)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(4)

        self.stop_segment_header = QWidget(self)
        self.stop_segment_header.setFixedHeight(30)
        self.stop_segment_header_layout = QHBoxLayout(self.stop_segment_header)
        self.stop_segment_header_layout.setContentsMargins(8, 0, 6, 0)
        self.stop_segment_header_layout.setSpacing(4)
        self.stop_segment_title_label = QLabel("의심구간", self)
        self.stop_segment_title_label.setObjectName("stopSegmentTitle")
        self.stop_segment_header_layout.addWidget(self.stop_segment_title_label)
        self.stop_segment_count_label = QLabel("0", self)
        self.stop_segment_count_label.setObjectName("mutedText")
        self.stop_segment_header_layout.addWidget(self.stop_segment_count_label)
        self.stop_segment_header_layout.addStretch(1)
        self.stop_segment_header_layout.addWidget(self.detect_stop_button)

        self.stop_segment_list = QListWidget(self)
        self.stop_segment_list.setObjectName("stopSegmentList")
        self.stop_segment_list.setWordWrap(True)
        self.stop_segment_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.stop_segment_list.itemClicked.connect(self._seek_to_stop_segment_item)
        self.stop_segment_list.itemDoubleClicked.connect(self._seek_to_stop_segment_item)
        self.report_controls.append(self.stop_segment_list)

        self.stop_analysis_status_label = QLabel("", self)
        self.stop_analysis_status_label.setObjectName("stopAnalysisStatus")
        self.stop_analysis_status_label.setWordWrap(True)
        self.stop_analysis_status_label.hide()
        self.stop_analysis_progress_bar = QProgressBar(self)
        self.stop_analysis_progress_bar.setRange(0, 100)
        self.stop_analysis_progress_bar.setTextVisible(True)
        self.stop_analysis_progress_bar.setFixedHeight(14)
        self.stop_analysis_progress_bar.hide()

        layout.addWidget(self.stop_segment_header)
        layout.addWidget(self.stop_analysis_status_label)
        layout.addWidget(self.stop_analysis_progress_bar)
        layout.addWidget(self.stop_segment_list, 1)
        return panel

    def _start_stop_segment_panel_resize(self) -> None:
        self._stop_segment_resize_start_width = self.stop_segment_panel_width

    def _resize_stop_segment_panel(self, delta: int) -> None:
        self._set_stop_segment_panel_width(
            self._stop_segment_resize_start_width + delta
        )

    def _restore_stop_segment_panel(self) -> None:
        if self.stop_segment_panel_width <= 0:
            self._set_stop_segment_panel_width(STOP_SEGMENT_PANEL_DEFAULT_WIDTH)

    def _set_stop_segment_panel_width(self, width: int) -> None:
        if width <= STOP_SEGMENT_PANEL_COLLAPSE_THRESHOLD:
            target = 0
        else:
            target = max(
                STOP_SEGMENT_PANEL_MIN_WIDTH,
                min(STOP_SEGMENT_PANEL_MAX_WIDTH, width),
            )

        self.stop_segment_panel_width = target
        self.stop_segment_panel.setVisible(target > 0)
        self.stop_segment_panel.setFixedWidth(target)

        compact = 0 < target < STOP_SEGMENT_PANEL_COMPACT_WIDTH
        self.stop_segment_header.setVisible(target > 0)
        self.stop_segment_title_label.setVisible(not compact)
        self.stop_segment_count_label.setVisible(not compact)
        if compact:
            self.stop_segment_header_layout.setContentsMargins(2, 0, 2, 0)
            self.stop_segment_header_layout.setSpacing(0)
            self.detect_stop_button.setFixedWidth(max(34, target - 4))
        else:
            self.stop_segment_header_layout.setContentsMargins(8, 0, 6, 0)
            self.stop_segment_header_layout.setSpacing(4)
            self.detect_stop_button.setMinimumWidth(0)
            self.detect_stop_button.setMaximumWidth(16777215)
        self.detect_stop_button.updateGeometry()
        collapsed = target == 0
        self.stop_segment_resize_handle.setProperty(
            "collapsed", "true" if collapsed else "false"
        )
        self.stop_segment_resize_handle.style().unpolish(
            self.stop_segment_resize_handle
        )
        self.stop_segment_resize_handle.style().polish(
            self.stop_segment_resize_handle
        )

    def _build_right_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setObjectName("rightPanel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = QWidget(self)
        content.setObjectName("rightPanelContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 28)
        content_layout.setSpacing(16)
        content_layout.addWidget(self._build_page_header())
        self.right_stack = QStackedWidget(self)
        self.project_list_page = self._build_project_list_page()
        self.business_list_page = self._build_business_list_page()
        self.report_list_page = self._build_report_list_page()
        self.report_detail_page = self._build_report_detail_page()
        for page in (
            self.project_list_page,
            self.business_list_page,
            self.report_list_page,
            self.report_detail_page,
        ):
            self.right_stack.addWidget(page)
        content_layout.addWidget(self.right_stack)
        outer.addWidget(content)
        return panel

    def _build_page_header(self) -> QWidget:
        header = QWidget(self)
        header.setObjectName("pageShell")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.page_title_label = QLabel("프로젝트 목록", self)
        self.page_title_label.setObjectName("pageTitle")
        self.page_subtitle_label = QLabel("프로젝트별 사업과 보고서를 관리합니다.", self)
        self.page_subtitle_label.setObjectName("pageSubtitle")
        self.breadcrumb_label = QLabel("Pipe1 > 프로젝트", self)
        self.breadcrumb_label.setObjectName("breadcrumb")
        layout.addWidget(self.page_title_label)
        layout.addWidget(self.page_subtitle_label)
        layout.addWidget(self.breadcrumb_label)
        return header

    def _set_page_header(
        self, title: str, subtitle: str, breadcrumbs: list[str]
    ) -> None:
        if not hasattr(self, "page_title_label"):
            return
        self.page_title_label.setText(title)
        self.page_subtitle_label.setText(subtitle)
        self.breadcrumb_label.setText(" > ".join(breadcrumbs))

    def _build_entity_table(
        self,
        headers: list[str],
        column_widths: dict[int, int],
        stretch_columns: set[int] | None = None,
    ) -> QTableWidget:
        table = QTableWidget(0, len(headers), self)
        table.setObjectName("entityTable")
        table.setHorizontalHeaderLabels(headers)
        configure_table_headers(table)
        table.setColumnHidden(0, True)
        table.verticalHeader().hide()
        configure_table_rows(table, 46)
        apply_column_widths(table, column_widths, stretch_columns)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        return table

    def _build_content_card(self) -> tuple[QWidget, QVBoxLayout]:
        card = QWidget(self)
        card.setObjectName("contentCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        return card, layout

    def _make_action_button(
        self, text: str, callback, object_name: str
    ) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName(object_name)
        button.clicked.connect(callback)
        return button

    def _make_player_icon_button(
        self, icon_name: str, tooltip: str, callback=None
    ) -> QPushButton:
        button = QPushButton(self)
        button.setObjectName("playerIconButton")
        button.setIcon(make_player_icon(icon_name))
        button.setIconSize(QSize(24, 24))
        button.setFixedSize(28, 28)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        if callback is not None:
            button.clicked.connect(callback)
        return button

    def _set_player_button_icon(self, button: QPushButton, icon_name: str) -> None:
        button.setIcon(make_player_icon(icon_name))

    def _set_speed_button_text(self) -> None:
        if hasattr(self, "speed_button"):
            self.speed_button.setText(f"{self.playback_speed:.1f}X")

    def _cycle_speed(self) -> None:
        speed_values = [0.5, 1.0, 2.0]
        try:
            current_index = speed_values.index(self.playback_speed)
        except ValueError:
            current_index = 1
        next_speed = speed_values[(current_index + 1) % len(speed_values)]
        self._speed_changed(f"{next_speed:g}x")

    def _toggle_window_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _build_card_action_row(
        self,
        meta_label: QLabel,
        actions: list[tuple[str, object, str]],
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        meta_label.setObjectName("cardMeta")
        row.addWidget(meta_label)
        row.addStretch(1)
        for text, callback, object_name in actions:
            row.addWidget(self._make_action_button(text, callback, object_name))
        return row

    def _build_project_list_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("pageShell")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card, card_layout = self._build_content_card()
        self.project_count_label = QLabel("총 0건", self)
        card_layout.addLayout(
            self._build_card_action_row(
                self.project_count_label,
                [
                    ("프로젝트 생성", self.create_project_from_right, "primaryButton"),
                    ("선택 열기", self.open_selected_project_from_right, "secondaryButton"),
                    ("선택 복제", self.duplicate_selected_project_from_right, "secondaryButton"),
                    ("선택 수정", self.edit_selected_project_from_right, "secondaryButton"),
                    ("선택 삭제", self.delete_selected_project_from_right, "dangerButton"),
                ],
            )
        )
        self.project_table = self._build_entity_table(
            ["ID", "프로젝트명", "생성일", "사업 수", "보고서 수"],
            {0: 0, 2: 150, 3: 90, 4: 90},
            {1},
        )
        self.project_table.itemDoubleClicked.connect(
            lambda _item: self.open_selected_project_from_right()
        )
        card_layout.addWidget(self.project_table)
        layout.addWidget(card)
        return page

    def _build_business_list_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("pageShell")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card, card_layout = self._build_content_card()
        self.business_count_label = QLabel("총 0건", self)
        card_layout.addLayout(
            self._build_card_action_row(
                self.business_count_label,
                [
                    ("사업 생성", self.create_business_from_right, "primaryButton"),
                    ("선택 열기", self.open_selected_business_from_right, "secondaryButton"),
                    ("선택 복제", self.duplicate_selected_business_from_right, "secondaryButton"),
                    ("선택 수정", self.edit_selected_business_from_right, "secondaryButton"),
                    ("선택 삭제", self.delete_selected_business_from_right, "dangerButton"),
                ],
            )
        )
        self.business_table = self._build_entity_table(
            ["ID", "사업코드", "사업명", "발주처", "시작일", "완료일", "보고서 수"],
            {0: 0, 1: 90, 3: 130, 4: 120, 5: 120, 6: 90},
            {2},
        )
        self.business_table.itemDoubleClicked.connect(
            lambda _item: self.open_selected_business_from_right()
        )
        card_layout.addWidget(self.business_table)
        layout.addWidget(card)
        return page

    def _build_report_list_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("pageShell")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card, card_layout = self._build_content_card()
        self.report_count_label = QLabel("총 0건", self)
        card_layout.addLayout(
            self._build_card_action_row(
                self.report_count_label,
                [
                    ("보고서 생성", self.create_report_from_right, "primaryButton"),
                    ("선택 열기", self.open_selected_report_from_right, "secondaryButton"),
                    ("선택 복제", self.duplicate_selected_report_from_right, "secondaryButton"),
                    ("선택 수정", self.edit_selected_report_from_right, "secondaryButton"),
                    ("선택 삭제", self.delete_selected_report_from_right, "dangerButton"),
                ],
            )
        )
        self.report_table = self._build_entity_table(
            [
                "ID",
                "보고서번호",
                "관로번호",
                "조사일자",
                "연장(m)",
                "총주행거리(m)",
                "완주여부",
                "영상",
                "결함 수",
                "버전 수",
            ],
            {
                0: 0,
                1: 115,
                2: 115,
                3: 120,
                4: 95,
                5: 120,
                6: 85,
                7: 70,
                8: 80,
                9: 80,
            },
            {2},
        )
        self.report_table.itemDoubleClicked.connect(
            lambda _item: self.open_selected_report_from_right()
        )
        card_layout.addWidget(self.report_table)
        layout.addWidget(card)
        return page

    def _build_report_detail_page(self) -> QWidget:
        panel = QWidget(self)
        panel.setObjectName("pageShell")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        card, card_layout = self._build_content_card()
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        self.right_layout = QVBoxLayout(content)
        self.right_layout.setContentsMargins(12, 12, 12, 12)
        self.right_layout.setSpacing(12)
        scroll.setWidget(content)
        card_layout.addWidget(scroll)
        outer.addWidget(card)

        self.right_layout.addWidget(self._build_report_export_bar())
        self.right_layout.addWidget(self._build_section_title("• 보고서 정보"))
        self.right_layout.addWidget(self._build_report_info_table())
        self.right_layout.addSpacing(12)
        self.right_layout.addWidget(self._build_section_title("• 관로 정보"))
        self.right_layout.addWidget(self._build_pipe_manhole_table())
        self.right_layout.addWidget(self._build_actual_survey_table())

        self.video_info_table = self._build_summary_table()
        self.video_info_table.hide()
        self.right_layout.addSpacing(12)
        self.right_layout.addWidget(self.center_panel)
        self.right_layout.addWidget(self._build_defect_form_group())
        self.right_layout.addWidget(self._build_defect_table_group())
        self._configure_report_detail_tab_order()
        self.right_layout.addStretch(1)
        return panel

    def _build_report_export_bar(self) -> QWidget:
        bar = QWidget(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        self.report_export_button = QPushButton("보고서 생성")
        self.report_export_button.clicked.connect(self.open_report_export_dialog)
        self.report_export_button.setObjectName("exportButton")
        layout.addWidget(self.report_export_button)
        self.report_controls.append(self.report_export_button)
        return bar

    def _build_video_section_header(self) -> QWidget:
        header = QWidget(self)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_section_title("• 영상 / 캡쳐 화면"))
        layout.addStretch(1)
        return header

    def _wrap_nav_group(self, title: str, widget: QWidget) -> QGroupBox:
        return NavSectionGroup(title, widget, self)

    def _build_section_title(self, title: str) -> QLabel:
        label = QLabel(title, self)
        label.setObjectName("sectionTitle")
        return label

    def _build_summary_table(self) -> QTableWidget:
        table = QTableWidget(4, 4, self)
        table.horizontalHeader().hide()
        table.verticalHeader().hide()
        configure_table_rows(table, 38)
        apply_column_widths(table, {0: 130, 1: 220, 2: 130, 3: 220})
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setFixedHeight(160)
        return table

    def _build_report_info_table(self) -> QTableWidget:
        table = QTableWidget(len(REPORT_INFO_TABLE_ROWS), 6, self)
        table.setObjectName("reportInfoTable")
        table.horizontalHeader().hide()
        table.verticalHeader().hide()
        configure_table_rows(table, 40)
        apply_column_widths(
            table,
            {0: 104, 1: 230, 2: 104, 3: 230, 4: 104, 5: 230},
            {1, 3, 5},
        )
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for row, fields in enumerate(REPORT_INFO_TABLE_ROWS):
            for pair_index, (field, label) in enumerate(fields):
                label_col = pair_index * 2
                value_col = label_col + 1
                table.setItem(row, label_col, read_only_table_item(label, is_label=True))
                edit = QLineEdit(self)
                configure_line_edit(edit)
                edit.textChanged.connect(self.update_export_state)
                edit.textChanged.connect(self.schedule_report_autosave)
                self.report_inputs[field] = edit
                self.report_controls.append(edit)
                table.setCellWidget(row, value_col, edit)
        fit_table_height_to_contents(table)
        return table

    def _build_pipe_manhole_table(self) -> QTableWidget:
        self.pipe_manhole_table = QTableWidget(3, 12, self)
        self.pipe_manhole_table.setHorizontalHeaderLabels(
            [
                "",
                "맨홀번호",
                "맨홀종류",
                "맨홀 내부재질",
                "맨홀 뚜껑재질",
                "맨홀 E.L(m)",
                "맨홀크기",
                "맨홀깊이(m)",
                "맨홀 인버트",
                "사다리모양",
                "위도",
                "경도",
            ]
        )
        configure_table_headers(self.pipe_manhole_table)
        self.pipe_manhole_table.verticalHeader().hide()
        configure_table_rows(self.pipe_manhole_table, 38)
        apply_column_widths(
            self.pipe_manhole_table,
            {
                0: 86,
                1: 84,
                2: 84,
                3: 112,
                4: 94,
                5: 78,
                6: 78,
                7: 84,
                8: 88,
                9: 112,
                10: 78,
                11: 78,
            },
            {2, 3, 4, 8, 9},
        )
        self.pipe_manhole_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.pipe_manhole_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.pipe_manhole_table.setRowHeight(2, 42)
        for row, role, label in (
            (0, "upstream", "상류맨홀*"),
            (1, "downstream", "하류맨홀*"),
        ):
            self.pipe_manhole_table.setItem(row, 0, read_only_table_item(label, is_label=True))
            for col, (field, _label) in enumerate(MANHOLE_LABELS, start=1):
                if field in MANHOLE_DROPDOWN_OPTIONS:
                    edit = QComboBox(self)
                    edit.addItems(MANHOLE_DROPDOWN_OPTIONS[field])
                    configure_combo(edit)
                    edit.currentTextChanged.connect(self.update_export_state)
                    edit.currentTextChanged.connect(self.schedule_report_autosave)
                else:
                    edit = QLineEdit(self)
                    configure_line_edit(edit)
                    edit.textChanged.connect(self.update_export_state)
                    edit.textChanged.connect(self.schedule_report_autosave)
                if field == "manhole_number":
                    edit.textChanged.connect(self._update_actual_direction_labels)
                self.manhole_inputs[role][field] = edit
                self.report_controls.append(edit)
                self.pipe_manhole_table.setCellWidget(row, col, edit)

        self.length_input = QLineEdit(self)
        self.total_drive_input = QLineEdit(self)
        configure_line_edit(self.length_input, 90)
        configure_line_edit(self.total_drive_input, 90)
        self.completion_label = QLabel("미완주")
        self.undriven_label = QLabel("")
        for label in (self.completion_label, self.undriven_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(24)
        self.length_input.textChanged.connect(self._update_pipe_derived)
        self.length_input.textChanged.connect(self.schedule_report_autosave)
        self.total_drive_input.textChanged.connect(self._update_pipe_derived)
        self.total_drive_input.textChanged.connect(self.schedule_report_autosave)
        self.report_controls.extend([self.length_input, self.total_drive_input])

        self.pipe_manhole_table.setItem(2, 0, read_only_table_item("연장(m)*", is_label=True))
        self.pipe_manhole_table.setSpan(2, 1, 1, 2)
        self.pipe_manhole_table.setCellWidget(2, 1, self.length_input)
        self.pipe_manhole_table.setItem(
            2, 3, read_only_table_item("총주행거리(m)*", is_label=True)
        )
        self.pipe_manhole_table.setSpan(2, 4, 1, 2)
        self.pipe_manhole_table.setCellWidget(2, 4, self.total_drive_input)
        self.pipe_manhole_table.setItem(2, 6, read_only_table_item("완주여부", is_label=True))
        self.pipe_manhole_table.setSpan(2, 7, 1, 2)
        self.pipe_manhole_table.setCellWidget(2, 7, self.completion_label)
        self.pipe_manhole_table.setItem(
            2, 9, read_only_table_item("미주행거리(m)", is_label=True)
        )
        self.pipe_manhole_table.setSpan(2, 10, 1, 2)
        self.pipe_manhole_table.setCellWidget(2, 10, self.undriven_label)
        fit_table_height_to_contents(self.pipe_manhole_table)
        return self.pipe_manhole_table

    def _build_actual_survey_table(self) -> QTableWidget:
        self.actual_table = QTableWidget(3, 5, self)
        self.actual_table.setHorizontalHeaderLabels(
            ["", "주행방향->맨홀번호", "발생지점(m)", "미주행사유", "미주행사유설명"]
        )
        configure_table_headers(self.actual_table)
        self.actual_table.verticalHeader().hide()
        configure_table_rows(self.actual_table, 38)
        apply_column_widths(
            self.actual_table,
            {0: 104, 1: 180, 2: 110, 3: 140, 4: 360},
            {1, 4},
        )
        self.actual_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.actual_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.actual_table.setRowHeight(2, 40)
        self.actual_table.setItem(0, 0, read_only_table_item("시작->끝", is_label=True))
        self.actual_table.setItem(1, 0, read_only_table_item("끝->시작", is_label=True))
        self.start_direction_item = read_only_table_item("", row_index=0)
        self.end_direction_item = read_only_table_item("", row_index=1)
        self.actual_table.setItem(0, 1, self.start_direction_item)
        self.actual_table.setItem(1, 1, self.end_direction_item)
        self.start_occurrence_input = QLineEdit(self)
        self.end_occurrence_input = QLineEdit(self)
        self.start_reason_combo = QComboBox(self)
        self.end_reason_combo = QComboBox(self)
        configure_line_edit(self.start_occurrence_input)
        configure_line_edit(self.end_occurrence_input)
        configure_combo(self.start_reason_combo)
        configure_combo(self.end_reason_combo)
        self.start_reason_combo.addItems(UNDROVE_REASONS)
        self.end_reason_combo.addItems(UNDROVE_REASONS)
        self.start_reason_combo.currentTextChanged.connect(self.schedule_report_autosave)
        self.end_reason_combo.currentTextChanged.connect(self.schedule_report_autosave)
        self.start_reason_detail_input = QLineEdit(self)
        self.end_reason_detail_input = QLineEdit(self)
        configure_line_edit(self.start_reason_detail_input)
        configure_line_edit(self.end_reason_detail_input)
        for edit in (
            self.start_occurrence_input,
            self.end_occurrence_input,
            self.start_reason_detail_input,
            self.end_reason_detail_input,
        ):
            edit.textChanged.connect(self.schedule_report_autosave)
        self.actual_table.setCellWidget(0, 2, self.start_occurrence_input)
        self.actual_table.setCellWidget(0, 3, self.start_reason_combo)
        self.actual_table.setCellWidget(0, 4, self.start_reason_detail_input)
        self.actual_table.setCellWidget(1, 2, self.end_occurrence_input)
        self.actual_table.setCellWidget(1, 3, self.end_reason_combo)
        self.actual_table.setCellWidget(1, 4, self.end_reason_detail_input)
        self.actual_table.setItem(2, 0, read_only_table_item("조사내용", is_label=True))
        self.actual_table.setSpan(2, 1, 1, 4)
        self.survey_content_input = QLineEdit(self)
        configure_line_edit(self.survey_content_input)
        self.survey_content_input.textChanged.connect(self.schedule_report_autosave)
        self.actual_table.setCellWidget(2, 1, self.survey_content_input)
        self.report_controls.extend(
            [
                self.start_occurrence_input,
                self.end_occurrence_input,
                self.start_reason_combo,
                self.end_reason_combo,
                self.start_reason_detail_input,
                self.end_reason_detail_input,
                self.survey_content_input,
            ]
        )
        fit_table_height_to_contents(self.actual_table)
        return self.actual_table

    def _build_defect_form_group(self) -> QWidget:
        section = QWidget(self)
        section.setObjectName("defectSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._build_section_title("• 결함 등록"))

        self.defect_drive_direction_combo = QComboBox(self)
        self.defect_drive_direction_combo.addItems(["순주행", "역주행"])
        self.distance_input = QLineEdit(self)
        self.item_category_combo = QComboBox(self)
        self.item_category_combo.addItems(ITEM_CATEGORIES)
        self.condition_item_combo = PopupTablePickerButton("상태항목 선택", self)
        self.defect_item_combo = PopupTablePickerButton("이상항목 선택", self)
        self.condition_item_combo.set_allow_empty(True)
        self.defect_item_combo.set_allow_empty(True)
        self.grade_combo = QComboBox(self)
        self.quadrant_combo = QComboBox(self)
        self.quadrant_combo.addItems(QUADRANTS)
        self.manhole_defect_depth_input = QLineEdit(self)
        self.memo_input = QLineEdit(self)
        compact_widths = {
            self.defect_drive_direction_combo: 84,
            self.distance_input: 80,
            self.item_category_combo: 76,
            self.condition_item_combo: 170,
            self.defect_item_combo: 190,
            self.grade_combo: 64,
            self.quadrant_combo: 108,
            self.manhole_defect_depth_input: 82,
        }
        for widget, width in compact_widths.items():
            if isinstance(widget, QLineEdit):
                configure_line_edit(widget, width)
            elif isinstance(widget, QComboBox):
                configure_combo(widget, width)
        for widget, width in (
            (self.condition_item_combo, 170),
            (self.defect_item_combo, 190),
        ):
            widget.setMinimumWidth(width)
            widget.setMinimumHeight(28)
        configure_line_edit(self.memo_input)
        for widget in (
            self.defect_drive_direction_combo,
            self.distance_input,
            self.item_category_combo,
            self.condition_item_combo,
            self.defect_item_combo,
            self.grade_combo,
            self.quadrant_combo,
            self.manhole_defect_depth_input,
            self.memo_input,
        ):
            allow_cell_widget_shrink(widget)
        self.item_category_combo.currentTextChanged.connect(
            self._defect_category_changed
        )
        self.condition_item_combo.currentTextChanged.connect(
            self._condition_item_changed
        )
        self.defect_item_combo.currentTextChanged.connect(
            self._defect_item_changed
        )
        self.report_controls.extend(
            [
                self.defect_drive_direction_combo,
                self.distance_input,
                self.item_category_combo,
                self.condition_item_combo,
                self.defect_item_combo,
                self.grade_combo,
                self.quadrant_combo,
                self.manhole_defect_depth_input,
                self.memo_input,
            ]
        )
        first_row_fields = [
            ("방향", self.defect_drive_direction_combo),
            ("거리", self.distance_input),
            ("구분", self.item_category_combo),
            ("상태", self.condition_item_combo),
            ("이상", self.defect_item_combo),
            ("등급", self.grade_combo),
            ("사분면", self.quadrant_combo),
            ("결함깊이", self.manhole_defect_depth_input),
        ]
        self.defect_form_table = QTableWidget(2, len(first_row_fields) * 2, self)
        self.defect_form_table.horizontalHeader().hide()
        self.defect_form_table.verticalHeader().hide()
        configure_table_rows(self.defect_form_table, 46)
        self.defect_form_table.setRowHeight(1, 42)
        apply_column_widths(
            self.defect_form_table,
            {
                0: 54,
                1: 88,
                2: 50,
                3: 82,
                4: 52,
                5: 82,
                6: 52,
                7: 170,
                8: 52,
                9: 190,
                10: 56,
                11: 66,
                12: 66,
                13: 112,
                14: 76,
                15: 86,
            },
            {7, 9},
        )
        self.defect_form_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.defect_form_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for idx, (label, widget) in enumerate(first_row_fields):
            col = idx * 2
            self.defect_form_table.setItem(
                0, col, read_only_table_item(label, is_label=True)
            )
            self.defect_form_table.setCellWidget(0, col + 1, widget)
        self.defect_form_table.setItem(
            1, 0, read_only_table_item("메모", is_label=True)
        )
        self.defect_form_table.setSpan(1, 1, 1, len(first_row_fields) * 2 - 1)
        self.defect_form_table.setCellWidget(1, 1, self.memo_input)
        fit_table_height_to_contents(self.defect_form_table)
        layout.addWidget(self.defect_form_table)
        self._refresh_defect_taxonomy_controls()

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.save_defect_button = QPushButton("결함 저장 (Enter)")
        self.save_defect_button.setObjectName("primaryButton")
        self.save_defect_button.clicked.connect(self.save_defect)
        self.edit_defect_button = QPushButton("선택 결함 수정")
        self.edit_defect_button.setObjectName("secondaryButton")
        self.edit_defect_button.clicked.connect(self.edit_selected_defect)
        self.cancel_defect_edit_button = QPushButton("수정 취소")
        self.cancel_defect_edit_button.setObjectName("secondaryButton")
        self.cancel_defect_edit_button.clicked.connect(self.cancel_defect_edit)
        for widget in (
            self.save_defect_button,
            self.edit_defect_button,
            self.cancel_defect_edit_button,
        ):
            action_row.addWidget(widget)
            self.report_controls.append(widget)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        return section

    def _configure_report_detail_tab_order(self) -> None:
        widgets = self._report_detail_tab_widgets()
        for widget in widgets:
            if isinstance(widget, (QComboBox, PopupTablePickerButton)):
                self._install_tab_focus_popup(widget)
        for previous, current in zip(widgets, widgets[1:]):
            QWidget.setTabOrder(previous, current)

    def _report_detail_tab_widgets(self) -> list[QWidget]:
        widgets: list[QWidget] = []
        for fields in REPORT_INFO_TABLE_ROWS:
            for field, _label in fields:
                widget = self.report_inputs.get(field)
                if widget is not None:
                    widgets.append(widget)
        for role in ("upstream", "downstream"):
            for field, _label in MANHOLE_LABELS:
                widget = self.manhole_inputs[role].get(field)
                if widget is not None:
                    widgets.append(widget)
        widgets.extend(
            [
                self.length_input,
                self.total_drive_input,
                self.start_occurrence_input,
                self.start_reason_combo,
                self.start_reason_detail_input,
                self.end_occurrence_input,
                self.end_reason_combo,
                self.end_reason_detail_input,
                self.survey_content_input,
                self.defect_drive_direction_combo,
                self.distance_input,
                self.item_category_combo,
                self.condition_item_combo,
                self.defect_item_combo,
                self.grade_combo,
                self.quadrant_combo,
                self.manhole_defect_depth_input,
                self.memo_input,
                self.save_defect_button,
                self.edit_defect_button,
                self.cancel_defect_edit_button,
            ]
        )
        return widgets

    def _install_tab_focus_popup(self, widget: QWidget) -> None:
        widget.setProperty("openPopupOnTabFocus", True)
        widget.installEventFilter(self)

    def _defect_category_changed(self, _category: str) -> None:
        self._refresh_defect_taxonomy_controls()

    def _condition_item_changed(self, condition_item: str) -> None:
        if self._syncing_defect_item_state:
            return
        if condition_item:
            self._syncing_defect_item_state = True
            try:
                self.defect_item_combo.setCurrentText("")
            finally:
                self._syncing_defect_item_state = False
        self._update_defect_grade_options()

    def _defect_item_changed(self, _defect_item: str) -> None:
        if self._syncing_defect_item_state:
            return
        if self.defect_item_combo.currentText():
            self._syncing_defect_item_state = True
            try:
                self.condition_item_combo.setCurrentText("")
            finally:
                self._syncing_defect_item_state = False
        self._update_defect_grade_options()

    def _refresh_defect_taxonomy_controls(
        self,
        *,
        preferred_condition: object = None,
        preferred_defect: object = None,
        preferred_grade: object = None,
    ) -> None:
        category = self.item_category_combo.currentText() or ITEM_CATEGORIES[0]
        preferred_condition = (
            str(preferred_condition) if preferred_condition not in (None, "") else ""
        )
        preferred_defect = (
            str(preferred_defect) if preferred_defect not in (None, "") else ""
        )
        if preferred_defect:
            preferred_condition = ""
        elif preferred_condition:
            preferred_grade = None

        condition_items = condition_items_for_category(category)
        self._syncing_defect_item_state = True
        try:
            self.condition_item_combo.set_grid_items(condition_items, 3)
            self.condition_item_combo.setCurrentText(preferred_condition)

            definitions = defect_definitions_for_category(category)
            rows = [
                [
                    definition.item,
                    definition.defect_type,
                    definition.scores.get("대", ""),
                    definition.scores.get("중", ""),
                    definition.scores.get("소", ""),
                ]
                for definition in definitions
            ]
            self.defect_item_combo.set_table_items(
                ["항목", "결함종류", "대", "중", "소"], rows
            )
            self.defect_item_combo.setCurrentText(preferred_defect)
        finally:
            self._syncing_defect_item_state = False
        self._update_defect_grade_options(preferred_grade)

    def _update_defect_grade_options(self, preferred_grade: object = None) -> None:
        category = self.item_category_combo.currentText() or ITEM_CATEGORIES[0]
        defect_item = self.defect_item_combo.currentText()
        self.grade_combo.blockSignals(True)
        self.grade_combo.clear()
        if not defect_item:
            self.grade_combo.setEnabled(False)
            self.grade_combo.blockSignals(False)
            update_combo_popup_width(self.grade_combo)
            return
        grades = grades_for_defect(category, defect_item) or GRADE_ORDER.copy()
        current = (
            str(preferred_grade)
            if preferred_grade not in (None, "")
            else self.grade_combo.currentText()
        )
        target = current if current in grades else grades[0]
        self.grade_combo.addItems(grades)
        self.grade_combo.setCurrentText(target)
        self.grade_combo.setEnabled(True)
        self.grade_combo.blockSignals(False)
        update_combo_popup_width(self.grade_combo)

    def _current_defect_score(self) -> int | None:
        return defect_score(
            self.item_category_combo.currentText(),
            self.defect_item_combo.currentText(),
            self.grade_combo.currentText(),
        )

    def _normalized_defect_selection(
        self,
    ) -> tuple[str | None, str | None, str | None]:
        condition_item = self.condition_item_combo.currentText().strip() or None
        defect_item = self.defect_item_combo.currentText().strip() or None
        if defect_item is not None:
            return None, defect_item, self.grade_combo.currentText().strip() or None
        if condition_item is not None:
            return condition_item, None, None
        return None, None, None

    def _validated_defect_selection(
        self,
    ) -> tuple[str | None, str | None, str | None] | None:
        condition_item, defect_item, grade = self._normalized_defect_selection()
        if condition_item is None and defect_item is None:
            QMessageBox.warning(self, "결함", "상태항목 또는 이상항목을 선택하세요")
            return None
        if defect_item is None:
            return condition_item, None, None
        if grade not in {"대", "중", "소"}:
            QMessageBox.warning(self, "결함", "이상항목의 등급을 선택하세요")
            return None
        if (
            defect_score(self.item_category_combo.currentText(), defect_item, grade)
            is None
        ):
            QMessageBox.warning(
                self, "결함", "선택한 이상항목에서 사용할 수 없는 등급입니다"
            )
            return None
        return None, defect_item, grade

    def _build_defect_table_group(self) -> QWidget:
        section = QWidget(self)
        section.setObjectName("defectSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        header_row.addWidget(self._build_section_title("• 결함 목록"))
        header_row.addStretch(1)
        self.state_grade_summary_label = QLabel("구조등급 - / 운영등급 -", self)
        self.state_grade_summary_label.setObjectName("cardMeta")
        header_row.addWidget(self.state_grade_summary_label)
        self.delete_defect_button = QPushButton("선택 결함 삭제")
        self.delete_defect_button.setObjectName("dangerButton")
        self.delete_defect_button.clicked.connect(self.delete_selected_defect)
        header_row.addWidget(self.delete_defect_button)
        self.report_controls.append(self.delete_defect_button)
        layout.addLayout(header_row)
        self.defect_table = QTableWidget(0, 15, self)
        self.defect_table.setObjectName("defectTable")
        self.defect_table.verticalHeader().hide()
        self.defect_table.setHorizontalHeaderLabels(
            [
                "ID",
                "원시시각",
                "No",
                "시각",
                "주행방향",
                "거리(m)",
                "항목구분",
                "상태항목",
                "이상항목",
                "등급",
                "사분면",
                "맨홀결함깊이(m)",
                "점수",
                "메모",
                "이미지",
            ]
        )
        configure_table_headers(self.defect_table)
        self.defect_table.setColumnHidden(0, True)
        self.defect_table.setColumnHidden(1, True)
        configure_table_rows(self.defect_table, 42)
        apply_column_widths(
            self.defect_table,
            {
                0: 0,
                1: 0,
                2: 52,
                3: 105,
                4: 82,
                5: 82,
                6: 82,
                7: 130,
                8: 115,
                9: 58,
                10: 88,
                11: 112,
                12: 58,
                13: 180,
                14: 240,
            },
            {7, 8, 13, 14},
        )
        self.defect_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.defect_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.defect_table.doubleClicked.connect(self._edit_defect_from_table)
        fit_table_height_to_contents(self.defect_table)
        self.report_controls.append(self.defect_table)
        layout.addWidget(self.defect_table)

        self.unit_state_grade_table = QTableWidget(0, 9, self)
        self.unit_state_grade_table.setObjectName("defectTable")
        self.unit_state_grade_table.verticalHeader().hide()
        self.unit_state_grade_table.setHorizontalHeaderLabels(
            [
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
        )
        configure_table_headers(self.unit_state_grade_table)
        configure_table_rows(self.unit_state_grade_table, 48)
        self.unit_state_grade_table.setWordWrap(True)
        apply_column_widths(
            self.unit_state_grade_table,
            {
                0: 56,
                1: 360,
                2: 82,
                3: 140,
                4: 76,
                5: 76,
                6: 76,
                7: 76,
                8: 66,
            },
            {1},
        )
        self.unit_state_grade_table.setSelectionMode(
            QTableWidget.SelectionMode.NoSelection
        )
        self.unit_state_grade_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        fit_table_height_to_contents(self.unit_state_grade_table)
        layout.addWidget(self._build_section_title("• 단위구간 상태등급"))
        layout.addWidget(self.unit_state_grade_table)
        return section

    def _register_shortcuts(self) -> None:
        actions = [
            ("Space", self._handle_space_shortcut),
            ("3", lambda: self.set_grade("대")),
            ("2", lambda: self.set_grade("중")),
            ("1", lambda: self.set_grade("소")),
            ("Return", self._save_defect_from_shortcut),
            ("Delete", self.delete_selected_defect),
            (Qt.Key.Key_Left, lambda: self.step_frame(-1)),
            (Qt.Key.Key_Right, lambda: self.step_frame(1)),
            (QKeySequence(Qt.KeyboardModifier.ShiftModifier | Qt.Key.Key_Left), lambda: self.jump_seconds(-5)),
            (QKeySequence(Qt.KeyboardModifier.ShiftModifier | Qt.Key.Key_Right), lambda: self.jump_seconds(5)),
        ]
        self._shortcuts: list[QShortcut] = []
        for key, callback in actions:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(
                lambda cb=callback: self._run_shortcut_if_allowed(cb)
            )
            self._shortcuts.append(shortcut)

    def _run_shortcut_if_allowed(self, callback) -> None:
        if self._is_typing_in_textbox():
            return
        callback()

    def _save_defect_from_shortcut(self) -> None:
        if not self._is_report_detail_page_active():
            return
        self.save_defect()

    def _is_report_detail_page_active(self) -> bool:
        if not hasattr(self, "right_stack") or not hasattr(self, "report_detail_page"):
            return False
        return self.right_stack.currentWidget() == self.report_detail_page

    def _is_typing_in_textbox(self) -> bool:
        focused = QApplication.focusWidget()
        return isinstance(focused, QLineEdit)

    def _handle_space_shortcut(self) -> None:
        if self.is_playing:
            self.capture_frame()
        else:
            self.toggle_play()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.FocusIn:
            self._open_popup_on_tab_focus(obj, event)
        if obj in self.video_drop_targets and self._handle_video_drop_event(event):
            return True
        if obj == self.video_label and self.is_selecting_depth_roi:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.depth_drag_start = event.position().toPoint()
                    self.depth_drag_end = event.position().toPoint()
                    self._refresh_video_display()
                    return True
            elif event.type() == QEvent.Type.MouseMove:
                if self.depth_drag_start is not None:
                    self.depth_drag_end = event.position().toPoint()
                    self._refresh_video_display()
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.depth_drag_end = event.position().toPoint()
                    self.pending_depth_roi = self._display_drag_to_frame_roi()
                    if self.pending_depth_roi is not None:
                        self.current_depth_roi = self.pending_depth_roi
                        self.save_depth_roi_button.setEnabled(True)
                    self.depth_drag_start = None
                    self.depth_drag_end = None
                    self._refresh_video_display()
                    return True
        return super().eventFilter(obj, event)

    def _handle_video_drop_event(self, event) -> bool:
        if event.type() in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
        ):
            if self._dropped_video_path(event.mimeData()) is not None:
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.Type.Drop:
            file_path = self._dropped_video_path(event.mimeData())
            if file_path is not None:
                event.acceptProposedAction()
                self._register_video_file(file_path, confirm_replace=True)
                return True
        return False

    def _open_popup_on_tab_focus(self, obj, event) -> None:
        if not isinstance(obj, QWidget):
            return
        if not obj.property("openPopupOnTabFocus"):
            return
        if not obj.isEnabled() or not obj.isVisible():
            return
        if not self._is_report_detail_page_active():
            return
        if event.reason() not in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
        ):
            return
        QTimer.singleShot(0, lambda widget=obj: self._show_focus_popup(widget))

    def _show_focus_popup(self, widget: QWidget) -> None:
        if not widget.hasFocus() or not widget.isEnabled() or not widget.isVisible():
            return
        if isinstance(widget, QComboBox):
            if widget.count() > 0:
                widget.showPopup()
            return
        if isinstance(widget, PopupTablePickerButton):
            widget._show_popup()

    def _dropped_video_path(self, mime_data) -> Optional[Path]:
        if self.current_report_id is None or not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            file_path = Path(url.toLocalFile())
            if (
                file_path.is_file()
                and file_path.suffix.lower() in VIDEO_FILE_EXTENSIONS
            ):
                return file_path
        return None

    def selected_tree_item(self):
        for widget in (self.report_nav, self.business_nav, self.project_nav):
            items = widget.selectedItems()
            if items:
                return items[0]
        return None

    def _selected_table_id(self, table: QTableWidget) -> Optional[int]:
        row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def _set_entity_table_row(
        self, table: QTableWidget, row: int, entity_id: int, values: list[object]
    ) -> None:
        table.setItem(row, 0, read_only_table_item(entity_id, row_index=row))
        for col, value in enumerate(values, start=1):
            table.setItem(row, col, read_only_table_item(value, row_index=row))

    def _show_project_list(self) -> None:
        self.current_project_id = None
        self.current_business_id = None
        self.current_report_id = None
        self.current_version_group_id = None
        self._update_nav_title_clickable_states()
        self._set_page_header(
            "프로젝트 목록",
            "프로젝트별 사업과 보고서를 관리합니다.",
            ["Pipe1", "프로젝트"],
        )
        self.project_table.setRowCount(0)
        rows = self.db.list_projects_with_counts()
        self.project_count_label.setText(f"총 {len(rows)}건")
        self.project_table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            self._set_entity_table_row(
                self.project_table,
                idx,
                int(row["id"]),
                [
                    row["project_name"],
                    row["created_at"],
                    row["business_count"],
                    row["report_count"],
                ],
            )
        self.right_stack.setCurrentWidget(self.project_list_page)

    def _show_business_list(self, project_id: int) -> None:
        project = self.db.get_project(project_id)
        if project is None:
            self._show_project_list()
            return
        self.current_project_id = project_id
        self.current_business_id = None
        self.current_report_id = None
        self.current_version_group_id = None
        self._update_nav_title_clickable_states()
        self._set_page_header(
            f"{project['project_name']} - 사업 목록",
            "선택한 프로젝트의 사업을 생성하고 관리합니다.",
            ["Pipe1", project["project_name"]],
        )
        rows = self.db.list_businesses_with_counts(project_id)
        self.business_count_label.setText(f"총 {len(rows)}건")
        self.business_table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            self._set_entity_table_row(
                self.business_table,
                idx,
                int(row["id"]),
                [
                    row["business_code"],
                    row["business_name"],
                    row["client"] or "",
                    row["business_start_date"] or "",
                    row["business_end_date"] or "",
                    row["report_count"],
                ],
            )
        self.right_stack.setCurrentWidget(self.business_list_page)

    def _show_report_list(self, business_id: int) -> None:
        business = self.db.get_business(business_id)
        if business is None:
            self._show_project_list()
            return
        self.current_project_id = int(business["project_id"])
        self.current_business_id = business_id
        self.current_report_id = None
        self.current_version_group_id = None
        self._update_nav_title_clickable_states()
        project = self.db.get_project(self.current_project_id)
        project_name = project["project_name"] if project is not None else "프로젝트"
        self._set_page_header(
            f"{business['business_name']} - 보고서 목록",
            "선택한 사업의 보고서를 생성하고 조사 결과를 관리합니다.",
            [
                "Pipe1",
                project_name,
                f"{business['business_code']} / {business['business_name']}",
            ],
        )
        rows = self.db.list_reports_with_counts(business_id)
        self.report_count_label.setText(f"총 {len(rows)}건")
        self.report_table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            self._set_entity_table_row(
                self.report_table,
                idx,
                int(row["id"]),
                [
                    row["report_number"],
                    row["pipe_number"],
                    row["survey_date"] or "",
                    "" if row["length_m"] is None else f"{float(row['length_m']):.3f}",
                    ""
                    if row["total_drive_distance_m"] is None
                    else f"{float(row['total_drive_distance_m']):.3f}",
                    "완주" if row["is_completed"] else "미완주",
                    "있음" if row["video_id"] is not None else "없음",
                    row["defect_count"],
                    row["version_count"],
                ],
            )
        self.right_stack.setCurrentWidget(self.report_list_page)

    def _current_navigation_selection(self) -> tuple[str, int] | tuple[None, None]:
        item = self.selected_tree_item()
        if item is None:
            return None, None
        return str(item.data(ROLE_KIND)), int(item.data(ROLE_ID))

    def _set_navigation_blocked(self, blocked: bool) -> None:
        self._updating_navigation = blocked
        for widget in (self.project_nav, self.business_nav, self.report_nav):
            widget.blockSignals(blocked)

    def _find_nav_item(self, widget: QListWidget, entity_id: int) -> QListWidgetItem | None:
        preferred: QListWidgetItem | None = None
        for row in range(widget.count()):
            item = widget.item(row)
            if int(item.data(ROLE_ID)) == entity_id:
                if item.data(ROLE_NAV_TYPE) == "report_version":
                    return item
                preferred = item
        if preferred is not None:
            return preferred
        for row in range(widget.count()):
            item = widget.item(row)
            group_id = item.data(ROLE_VERSION_GROUP_ID)
            if group_id is not None and int(group_id) == entity_id:
                return item
        return None

    def _set_nav_selection(self, widget: QListWidget, entity_id: int | None) -> bool:
        if entity_id is None:
            widget.clearSelection()
            widget.setCurrentRow(-1)
            return False
        item = self._find_nav_item(widget, entity_id)
        if item is None:
            widget.clearSelection()
            widget.setCurrentRow(-1)
            return False
        widget.setCurrentItem(item)
        item.setSelected(True)
        return True

    def _populate_project_nav(self) -> None:
        self.project_nav.clear()
        try:
            projects = self.db.list_projects()
        except sqlite3.Error as exc:
            self._handle_database_error(exc)
            return
        self._database_error_reported = False
        for project in projects:
            self.project_nav.addItem(
                self.project_nav.create_item(project["id"], project["project_name"])
            )

    def _populate_business_nav(self, project_id: int | None) -> None:
        self.business_nav.clear()
        if project_id is None:
            return
        try:
            businesses = self.db.list_businesses(project_id)
        except sqlite3.Error as exc:
            self._handle_database_error(exc)
            return
        self._database_error_reported = False
        for business in businesses:
            label = f"{business['business_code']} - {business['business_name']}"
            self.business_nav.addItem(self.business_nav.create_item(business["id"], label))

    @staticmethod
    def _report_version_nav_label(version) -> str:
        version_name = version["version_name"] or f"v{version['version_number']}"
        updated_at = version["updated_at"] or "-"
        return f"{version_name} · 마지막 수정: {updated_at}"

    def _set_report_group_item_widget(
        self,
        item: QListWidgetItem,
        label: str,
        version_group_id: int,
    ) -> None:
        expanded = version_group_id in self._expanded_report_version_groups
        widget = ReportGroupNavWidget(
            label,
            expanded=expanded,
            parent=self.report_nav,
        )
        widget.toggle_button.clicked.connect(
            lambda _checked=False, group_id=version_group_id: (
                self._toggle_report_versions(group_id)
            )
        )
        item.setText("")
        item.setToolTip(label)
        self.report_nav.setItemWidget(item, widget)

    def _populate_report_nav(self, business_id: int | None) -> None:
        self.report_nav.clear()
        if business_id is None:
            return
        try:
            reports = self.db.list_reports(business_id)
        except sqlite3.Error as exc:
            self._handle_database_error(exc)
            return
        self._database_error_reported = False
        for report in reports:
            label = f"{report['report_number']} / {report['pipe_number']}"
            version_group_id = int(report["version_group_id"])
            self.report_nav.addItem(
                self.report_nav.create_item(
                    int(report["id"]),
                    label,
                    nav_type="report_group",
                    version_group_id=version_group_id,
                )
            )
            group_item = self.report_nav.item(self.report_nav.count() - 1)
            self._set_report_group_item_widget(
                group_item,
                label,
                version_group_id,
            )
            if version_group_id not in self._expanded_report_version_groups:
                continue
            for version in self.db.list_report_versions(version_group_id):
                self.report_nav.addItem(
                    self.report_nav.create_item(
                        int(version["id"]),
                        self._report_version_nav_label(version),
                        nav_type="report_version",
                        version_group_id=version_group_id,
                    )
                )

    def _select_report_group_nav_item(self, version_group_id: int) -> bool:
        for row in range(self.report_nav.count()):
            item = self.report_nav.item(row)
            if item.data(ROLE_NAV_TYPE) != "report_group":
                continue
            if int(item.data(ROLE_VERSION_GROUP_ID) or -1) != version_group_id:
                continue
            self.report_nav.setCurrentItem(item)
            item.setSelected(True)
            return True
        return False

    def _toggle_report_versions(self, version_group_id: int) -> None:
        if version_group_id in self._expanded_report_version_groups:
            self._expanded_report_version_groups.remove(version_group_id)
        else:
            self._expanded_report_version_groups.add(version_group_id)

        business_id = self.current_business_id
        if business_id is None:
            business_item = self.business_nav.currentItem()
            business_id = (
                int(business_item.data(ROLE_ID))
                if business_item
                else None
            )
        if business_id is None:
            return

        self._set_navigation_blocked(True)
        try:
            self._populate_report_nav(business_id)
            selected = False
            if self.current_report_id is not None:
                selected = self._set_nav_selection(
                    self.report_nav,
                    self.current_report_id,
                )
            if not selected:
                self._select_report_group_nav_item(version_group_id)
        finally:
            self._set_navigation_blocked(False)

    def _handle_database_error(self, exc: sqlite3.Error) -> None:
        self.logger.exception("Database access failed")
        if self._database_error_reported:
            return
        self._database_error_reported = True
        db_path = getattr(self.db, "db_path", None)
        workspace = self.inspection.storage.workspace_root
        QMessageBox.critical(
            self,
            "DB 오류",
            "데이터베이스 파일을 열 수 없습니다.\n\n"
            f"작업 폴더: {workspace}\n"
            f"DB 파일: {db_path}\n\n"
            "작업 폴더가 삭제/이동됐거나 접근 권한이 없을 수 있습니다. "
            "작업 폴더 변경으로 올바른 폴더를 다시 선택하세요.\n\n"
            f"{exc}",
        )

    def refresh_tree(
        self,
        select_kind: str | None = None,
        select_id: int | None = None,
        *,
        flush_pending: bool = True,
    ) -> bool:
        if flush_pending and not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return False

        if select_kind is None:
            select_kind, select_id = self._current_navigation_selection()

        project_id: int | None = None
        business_id: int | None = None
        report_id: int | None = None
        if select_kind == "project" and select_id is not None:
            if self.db.get_project(select_id) is not None:
                project_id = select_id
        elif select_kind == "business" and select_id is not None:
            business = self.db.get_business(select_id)
            if business is not None:
                project_id = int(business["project_id"])
                business_id = select_id
        elif select_kind == "report" and select_id is not None:
            context = self.db.get_report_context(select_id)
            if context is not None:
                project_id = int(context["project_id"])
                business_id = int(context["business_id"])
                report_id = select_id
                self._expanded_report_version_groups.add(
                    int(context["version_group_id"])
                )

        self._set_navigation_blocked(True)
        try:
            self._populate_project_nav()
            project_selected = self._set_nav_selection(self.project_nav, project_id)
            self._populate_business_nav(project_id if project_selected else None)
            business_selected = self._set_nav_selection(self.business_nav, business_id)
            self._populate_report_nav(business_id if business_selected else None)
            report_selected = self._set_nav_selection(self.report_nav, report_id)
        finally:
            self._set_navigation_blocked(False)

        if report_id is not None and report_selected:
            self.load_report(report_id, flush_pending=False)
        elif business_id is not None and business_selected:
            self._reset_report_workspace(flush_pending=False)
            self._show_report_list(business_id)
        elif project_id is not None and project_selected:
            self._reset_report_workspace(flush_pending=False)
            self._show_business_list(project_id)
        else:
            self._reset_report_workspace(flush_pending=False)
            self._show_project_list()
        return True

    def _restore_current_navigation_selection(self) -> None:
        project_id = self.current_project_id
        business_id = self.current_business_id
        report_id = self.current_report_id

        if report_id is not None:
            context = self.db.get_report_context(report_id)
            if context is not None:
                project_id = int(context["project_id"])
                business_id = int(context["business_id"])
        elif business_id is not None:
            business = self.db.get_business(business_id)
            if business is not None:
                project_id = int(business["project_id"])

        self._set_navigation_blocked(True)
        try:
            self._populate_project_nav()
            project_selected = self._set_nav_selection(self.project_nav, project_id)
            self._populate_business_nav(project_id if project_selected else None)
            business_selected = self._set_nav_selection(self.business_nav, business_id)
            self._populate_report_nav(business_id if business_selected else None)
            self._set_nav_selection(
                self.report_nav,
                report_id if business_selected else None,
            )
        finally:
            self._set_navigation_blocked(False)

    def _require_table_selection(
        self, table: QTableWidget, label: str
    ) -> Optional[int]:
        entity_id = self._selected_table_id(table)
        if entity_id is None:
            QMessageBox.information(self, "선택 필요", f"{label}를 선택하세요")
        return entity_id

    def create_project_from_right(self) -> None:
        dialog = ProjectDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        name = dialog.value()
        if not name:
            return
        project_id = self.db.create_project(name)
        self.refresh_tree("project", project_id)

    def duplicate_selected_project_from_right(self) -> None:
        project_id = self._require_table_selection(self.project_table, "프로젝트")
        if project_id is None:
            return
        new_project_id = self.db.duplicate_project(project_id)
        self.refresh_tree("project", new_project_id)

    def duplicate_current_project_from_right(self) -> None:
        if self.current_project_id is None:
            return
        new_project_id = self.db.duplicate_project(self.current_project_id)
        self.refresh_tree("project", new_project_id)

    def edit_selected_project_from_right(self) -> None:
        project_id = self._require_table_selection(self.project_table, "프로젝트")
        if project_id is not None:
            self._edit_project(project_id)

    def edit_current_project_from_right(self) -> None:
        if self.current_project_id is not None:
            self._edit_project(self.current_project_id)

    def _edit_project(self, project_id: int) -> None:
        row = self.db.get_project(project_id)
        if row is None:
            return
        dialog = ProjectDialog(project_name=row["project_name"], parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        name = dialog.value()
        if not name:
            return
        self.db.update_project(project_id, name)
        self.refresh_tree("project", project_id)

    def delete_selected_project_from_right(self) -> None:
        project_id = self._require_table_selection(self.project_table, "프로젝트")
        if project_id is None:
            return
        if QMessageBox.question(self, "확인", "선택한 프로젝트와 작업 폴더를 삭제하시겠습니까?") != QMessageBox.StandardButton.Yes:
            return
        self.inspection.delete_project_with_artifacts(project_id)
        self.refresh_tree()

    def open_selected_project_from_right(self) -> None:
        project_id = self._require_table_selection(self.project_table, "프로젝트")
        if project_id is not None:
            self._select_tree_entity("project", project_id)

    def create_business_from_right(self) -> None:
        if self.current_project_id is None:
            QMessageBox.information(self, "선택 필요", "프로젝트를 선택하세요")
            return
        dialog = BusinessDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        payload = dialog.values()
        if not (payload.business_code and payload.business_name):
            return
        try:
            business_id = self.db.create_business(
                self.current_project_id,
                payload.business_code,
                payload.business_name,
                payload.client or None,
                payload.business_start_date or None,
                payload.business_end_date or None,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "사업 오류", str(exc))
            return
        self.refresh_tree("business", business_id)

    def duplicate_selected_business_from_right(self) -> None:
        business_id = self._require_table_selection(self.business_table, "사업")
        if business_id is None:
            return
        new_business_id = self.db.duplicate_business(
            business_id, self.current_project_id
        )
        self.refresh_tree("business", new_business_id)

    def duplicate_current_business_from_right(self) -> None:
        if self.current_business_id is None:
            return
        new_business_id = self.db.duplicate_business(self.current_business_id)
        self.refresh_tree("business", new_business_id)

    def edit_selected_business_from_right(self) -> None:
        business_id = self._require_table_selection(self.business_table, "사업")
        if business_id is not None:
            self._edit_business(business_id)

    def edit_current_business_from_right(self) -> None:
        if self.current_business_id is not None:
            self._edit_business(self.current_business_id)

    def _edit_business(self, business_id: int) -> None:
        row = self.db.get_business(business_id)
        if row is None:
            return
        dialog = BusinessDialog(
            business_code=row["business_code"],
            business_name=row["business_name"],
            client=row["client"] or "",
            business_start_date=row["business_start_date"] or "",
            business_end_date=row["business_end_date"] or "",
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        payload = dialog.values()
        if not (payload.business_code and payload.business_name):
            return
        try:
            self.db.update_business(
                business_id,
                payload.business_code,
                payload.business_name,
                payload.client or None,
                payload.business_start_date or None,
                payload.business_end_date or None,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "사업 오류", str(exc))
            return
        self.refresh_tree("business", business_id)

    def delete_selected_business_from_right(self) -> None:
        business_id = self._require_table_selection(self.business_table, "사업")
        if business_id is None:
            return
        project_id = self.current_project_id
        if QMessageBox.question(self, "확인", "선택한 사업과 작업 폴더를 삭제하시겠습니까?") != QMessageBox.StandardButton.Yes:
            return
        self.inspection.delete_business_with_artifacts(business_id)
        if project_id is not None:
            self.refresh_tree("project", project_id)
        else:
            self.refresh_tree()

    def open_selected_business_from_right(self) -> None:
        business_id = self._require_table_selection(self.business_table, "사업")
        if business_id is not None:
            self._select_tree_entity("business", business_id)

    def create_report_from_right(self) -> None:
        if self.current_business_id is None:
            QMessageBox.information(self, "선택 필요", "사업을 선택하세요")
            return
        dialog = ReportDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        payload = dialog.values()
        if not (payload.report_number and payload.pipe_number):
            return
        try:
            report_id = self.db.create_report(
                self.current_business_id, payload.report_number, payload.pipe_number
            )
        except ValueError as exc:
            QMessageBox.warning(self, "보고서 오류", str(exc))
            return
        self.refresh_tree("report", report_id)

    def duplicate_selected_report_from_right(self) -> None:
        report_id = self._require_table_selection(self.report_table, "보고서")
        if report_id is None:
            return
        new_report_id = self.db.duplicate_report(report_id, self.current_business_id)
        self.refresh_tree("report", new_report_id)

    def duplicate_current_report_from_right(self) -> None:
        if self.current_report_id is None:
            return
        new_report_id = self.db.duplicate_report(
            self.current_report_id, self.current_business_id
        )
        self.refresh_tree("report", new_report_id)

    def edit_selected_report_from_right(self) -> None:
        report_id = self._require_table_selection(self.report_table, "보고서")
        if report_id is not None:
            self._edit_report(report_id)

    def edit_current_report_from_right(self) -> None:
        if self.current_report_id is not None:
            self._edit_report(self.current_report_id)

    def _edit_report(self, report_id: int) -> None:
        row = self.db.get_report(report_id)
        if row is None:
            return
        dialog = ReportDialog(
            report_number=row["report_number"],
            pipe_number=row["pipe_number"],
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        payload = dialog.values()
        if not (payload.report_number and payload.pipe_number):
            return
        data = {field: row[field] for field in REPORT_FIELDS}
        data["report_number"] = payload.report_number
        data["pipe_number"] = payload.pipe_number
        try:
            self.db.update_report(report_id, data)
        except ValueError as exc:
            QMessageBox.warning(self, "보고서 오류", str(exc))
            return
        self.refresh_tree("report", report_id)

    def delete_selected_report_from_right(self) -> None:
        report_id = self._require_table_selection(self.report_table, "보고서")
        if report_id is None:
            return
        business_id = self.current_business_id
        if QMessageBox.question(self, "확인", "선택한 보고서와 작업 폴더를 삭제하시겠습니까?") != QMessageBox.StandardButton.Yes:
            return
        self.inspection.delete_report_with_artifacts(report_id)
        if business_id is not None:
            self.refresh_tree("business", business_id)
        else:
            self.refresh_tree()

    def delete_current_report_from_right(self) -> None:
        if self.current_report_id is None:
            return
        report_id = self.current_report_id
        business_id = self.current_business_id
        if QMessageBox.question(self, "확인", "현재 보고서와 작업 폴더를 삭제하시겠습니까?") != QMessageBox.StandardButton.Yes:
            return
        self.inspection.delete_report_with_artifacts(report_id)
        self.current_report_id = None
        self._reset_report_workspace()
        if business_id is not None:
            self.refresh_tree("business", business_id)
        else:
            self.refresh_tree()

    def open_selected_report_from_right(self) -> None:
        report_id = self._require_table_selection(self.report_table, "보고서")
        if report_id is not None:
            self._select_tree_entity("report", report_id)

    def show_current_report_list_from_right(self) -> None:
        if self.current_business_id is not None:
            self._select_tree_entity("business", self.current_business_id)

    def _select_tree_entity(self, kind: str, entity_id: int) -> bool:
        if not self.refresh_tree(kind, entity_id):
            return False
        selected_kind, selected_id = self._current_navigation_selection()
        return selected_kind == kind and selected_id == entity_id

    def change_workspace_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "새 작업 폴더 선택", str(self.inspection.storage.workspace_root)
        )
        if not selected:
            return
        new_workspace = Path(selected)
        if not new_workspace.exists() or not new_workspace.is_dir():
            QMessageBox.warning(self, "작업 폴더", "유효한 폴더가 아닙니다")
            return
        if not self._flush_pending_report_autosave():
            return
        self.stop_playback()
        self._release_video()
        new_db = Database(new_workspace / "sewerpipe_inspector.db")
        self.db = new_db
        self.inspection.db = new_db
        self.inspection.storage = StorageService(new_workspace)
        configure_logging(new_workspace / "error.log")
        self._database_error_reported = False
        self._reset_report_workspace()
        self.refresh_tree()

        ask_default = QMessageBox()
        ask_default.setIcon(QMessageBox.Icon.Question)
        ask_default.setWindowTitle("기본 폴더 설정")
        ask_default.setText("이 폴더를 기본 작업 폴더로 저장하시겠습니까?")
        ask_default.setInformativeText(str(new_workspace))
        ask_default.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ask_default.exec() == QMessageBox.StandardButton.Yes:
            settings = load_settings()
            settings.default_workspace = str(new_workspace)
            settings.always_show_directory_picker = False
            settings.suppress_default_workspace_prompt = True
            save_settings(settings)

    def _project_selection_changed(self) -> None:
        if self._updating_navigation:
            return
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        items = self.project_nav.selectedItems()
        current = items[0] if items else None
        self._set_navigation_blocked(True)
        try:
            self.business_nav.clear()
            self.report_nav.clear()
            project_id = int(current.data(ROLE_ID)) if current is not None else None
            self._populate_business_nav(project_id)
        finally:
            self._set_navigation_blocked(False)
        self._reset_report_workspace(flush_pending=False)
        if project_id is None:
            self._show_project_list()
            return
        self._show_business_list(project_id)

    def _show_project_root_from_nav(self) -> None:
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        self._set_navigation_blocked(True)
        try:
            self.project_nav.clearSelection()
            self.project_nav.setCurrentRow(-1)
            self.business_nav.clear()
            self.report_nav.clear()
        finally:
            self._set_navigation_blocked(False)
        self._reset_report_workspace(flush_pending=False)
        self._show_project_list()

    def _show_business_root_from_nav(self) -> None:
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        if self.current_project_id is None:
            return
        project_id = self.current_project_id
        self._set_navigation_blocked(True)
        try:
            self.business_nav.clearSelection()
            self.business_nav.setCurrentRow(-1)
            self.report_nav.clear()
        finally:
            self._set_navigation_blocked(False)
        self._reset_report_workspace(flush_pending=False)
        self._show_business_list(project_id)

    def _show_report_root_from_nav(self) -> None:
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        if self.current_business_id is None:
            return
        business_id = self.current_business_id
        self._set_navigation_blocked(True)
        try:
            self.report_nav.clearSelection()
            self.report_nav.setCurrentRow(-1)
        finally:
            self._set_navigation_blocked(False)
        self._reset_report_workspace(flush_pending=False)
        self._show_report_list(business_id)

    def _update_nav_title_clickable_states(self) -> None:
        if not hasattr(self, "project_nav_group"):
            return
        self.project_nav_group.set_title_clickable(True)
        self.business_nav_group.set_title_clickable(self.current_project_id is not None)
        self.report_nav_group.set_title_clickable(self.current_business_id is not None)

    def _business_selection_changed(self) -> None:
        if self._updating_navigation:
            return
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        items = self.business_nav.selectedItems()
        current = items[0] if items else None
        self._set_navigation_blocked(True)
        try:
            self.report_nav.clear()
            business_id = int(current.data(ROLE_ID)) if current is not None else None
            self._populate_report_nav(business_id)
        finally:
            self._set_navigation_blocked(False)
        self._reset_report_workspace(flush_pending=False)
        if business_id is None:
            project_item = self.project_nav.currentItem()
            if project_item is not None:
                self._show_business_list(int(project_item.data(ROLE_ID)))
            else:
                self._show_project_list()
            return
        self._show_report_list(business_id)

    def _report_selection_changed(self) -> None:
        if self._updating_navigation:
            return
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        items = self.report_nav.selectedItems()
        current = items[0] if items else None
        if current is None:
            self._reset_report_workspace(flush_pending=False)
            business_item = self.business_nav.currentItem()
            if business_item is not None:
                self._show_report_list(int(business_item.data(ROLE_ID)))
            else:
                project_item = self.project_nav.currentItem()
                if project_item is not None:
                    self._show_business_list(int(project_item.data(ROLE_ID)))
                else:
                    self._show_project_list()
            return
        self.load_report(int(current.data(ROLE_ID)), flush_pending=False)

    def _show_report_nav_context_menu(self, pos: QPoint) -> None:
        item = self.report_nav.itemAt(pos)
        if item is None:
            return
        nav_type = item.data(ROLE_NAV_TYPE)
        version_group_id = item.data(ROLE_VERSION_GROUP_ID)
        report_id = item.data(ROLE_ID)
        if version_group_id is None or report_id is None:
            return
        version_group_id = int(version_group_id)
        report_id = int(report_id)
        menu = QMenu(self)
        delete_version_action = None
        delete_report_action = None
        create_version_action = menu.addAction("새 버전 생성")
        if nav_type == "report_version":
            delete_version_action = menu.addAction("버전 삭제")
        elif nav_type == "report_group":
            delete_report_action = menu.addAction("보고서 전체 삭제")
        action = menu.exec(self.report_nav.mapToGlobal(pos))
        if action is None:
            return
        if action == create_version_action:
            self.create_report_version_from_nav(int(version_group_id))
        elif delete_version_action is not None and action == delete_version_action:
            self.delete_report_version_from_nav(report_id, version_group_id)
        elif delete_report_action is not None and action == delete_report_action:
            self.delete_report_group_from_nav(report_id)

    def create_report_version_from_nav(self, version_group_id: int) -> None:
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        try:
            report_id = self.inspection.create_report_version_from_latest(
                version_group_id
            )
        except Exception as exc:
            self.logger.exception("Failed to create report version")
            QMessageBox.critical(self, "버전 생성 오류", str(exc))
            return
        self._expanded_report_version_groups.add(version_group_id)
        self.refresh_tree("report", report_id)

    def delete_report_version_from_nav(
        self,
        report_id: int,
        version_group_id: int,
    ) -> None:
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        report = self.db.get_report(report_id)
        if report is None:
            return
        version_name = report["version_name"] or f"v{report['version_number']}"
        if (
            QMessageBox.question(
                self,
                "버전 삭제",
                f"{version_name} 버전을 삭제하시겠습니까?\n다른 버전은 유지됩니다.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            next_report_id = self.inspection.delete_report_version_with_artifacts(
                report_id
            )
        except ValueError as exc:
            QMessageBox.warning(self, "버전 삭제", str(exc))
            return
        except Exception as exc:
            self.logger.exception("Failed to delete report version")
            QMessageBox.critical(self, "버전 삭제 오류", str(exc))
            return
        self._expanded_report_version_groups.add(version_group_id)
        if self.current_report_id == report_id:
            self.refresh_tree("report", next_report_id)
            return
        self.refresh_tree("report", self.current_report_id or next_report_id)

    def delete_report_group_from_nav(self, report_id: int) -> None:
        if not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return
        report = self.db.get_report(report_id)
        if report is None:
            return
        version_group_id = int(report["version_group_id"])
        business_id = self.current_business_id
        if business_id is None:
            context = self.db.get_report_context(report_id)
            if context is not None:
                business_id = int(context["business_id"])
        if (
            QMessageBox.question(
                self,
                "보고서 삭제",
                "이 보고서와 모든 버전, 작업 폴더를 삭제하시겠습니까?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.inspection.delete_report_with_artifacts(report_id)
        self._expanded_report_version_groups.discard(version_group_id)
        if self.current_version_group_id == version_group_id:
            self.current_report_id = None
            self._reset_report_workspace(flush_pending=False)
        if business_id is not None:
            self.refresh_tree("business", business_id)
        else:
            self.refresh_tree()

    def load_report(self, report_id: int, *, flush_pending: bool = True) -> bool:
        if flush_pending and not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return False
        report_changed = self.current_report_id != report_id
        if report_changed:
            self._clear_stop_segments()
        self.current_report_id = report_id
        self._set_report_controls_enabled(True)
        report = self.db.get_report(report_id)
        context = self.db.get_report_context(report_id)
        pipe_info = self.db.get_pipe_information(report_id)
        upstream = self.db.get_manhole(report_id, "upstream")
        downstream = self.db.get_manhole(report_id, "downstream")
        actual = self.db.get_actual_survey(report_id)
        if report is None or context is None:
            return False
        self.current_project_id = int(context["project_id"])
        self.current_business_id = int(context["business_id"])
        self.current_version_group_id = int(report["version_group_id"])
        version_name = report["version_name"] or f"v{report['version_number']}"
        self._update_nav_title_clickable_states()
        self._set_page_header(
            f"보고서 {report['report_number']} / {report['pipe_number']} · {version_name}",
            "선택한 버전 작업본의 보고서 정보, 영상, 결함 기록을 자동 저장합니다.",
            [
                "Pipe1",
                context["project_name"],
                f"{context['business_code']} / {context['business_name']}",
                f"{report['report_number']} / {report['pipe_number']}",
                version_name,
            ],
        )
        self.right_stack.setCurrentWidget(self.report_detail_page)
        self.center_panel.setVisible(True)

        self._loading_report = True
        try:
            for field in REPORT_FIELDS:
                if field in self.report_inputs:
                    self.report_inputs[field].setText("" if report[field] is None else str(report[field]))

            for role, row in (("upstream", upstream), ("downstream", downstream)):
                for field in MANHOLE_FIELDS:
                    widget = self.manhole_inputs[role].get(field)
                    if widget is not None:
                        set_input_widget_text(
                            widget, None if row is None else row[field]
                        )
            if pipe_info is not None:
                self.length_input.setText("" if pipe_info["length_m"] is None else str(pipe_info["length_m"]))
                self.total_drive_input.setText("" if pipe_info["total_drive_distance_m"] is None else str(pipe_info["total_drive_distance_m"]))
            else:
                self.length_input.clear()
                self.total_drive_input.clear()
            self._update_pipe_derived()

            if actual is not None:
                self.start_occurrence_input.setText(
                    "" if actual["start_occurrence_point_m"] is None else str(actual["start_occurrence_point_m"])
                )
                self.end_occurrence_input.setText(
                    "" if actual["end_occurrence_point_m"] is None else str(actual["end_occurrence_point_m"])
                )
                set_combo_text(self.start_reason_combo, actual["start_undriven_reason"], "없음")
                set_combo_text(self.end_reason_combo, actual["end_undriven_reason"], "없음")
                self.start_reason_detail_input.setText(actual["start_undriven_reason_detail"] or "")
                self.end_reason_detail_input.setText(actual["end_undriven_reason_detail"] or "")
                self.survey_content_input.setText(actual["survey_content"] or "")
            self._update_actual_direction_labels()
        finally:
            self._loading_report = False

        self.load_report_video()
        self.refresh_defects()
        self.update_summary()
        self.update_export_state()
        self._report_details_dirty = False
        return True

    def schedule_report_autosave(self, *_args) -> None:
        if self._loading_report or self.current_report_id is None:
            return
        self._report_details_dirty = True
        self.update_export_state()
        self.autosave_timer.start(700)

    def _autosave_report_details(self) -> None:
        self._flush_pending_report_autosave(show_errors=False)

    def _flush_pending_report_autosave(self, *, show_errors: bool = True) -> bool:
        if (
            self._loading_report
            or self.current_report_id is None
            or not self._report_details_dirty
        ):
            return True
        self.autosave_timer.stop()
        try:
            self.save_report_details_without_message()
        except ValueError as exc:
            if show_errors:
                QMessageBox.warning(self, "자동 저장 실패", str(exc))
            return False
        except Exception as exc:
            self.logger.exception("Failed to flush autosave")
            if show_errors:
                QMessageBox.critical(self, "자동 저장 실패", str(exc))
            return False
        self._update_current_report_nav_label()
        self.update_export_state()
        return True

    def _update_current_report_nav_label(self) -> None:
        if self.current_report_id is None:
            return
        report = self.db.get_report(self.current_report_id)
        if report is None:
            return
        version_group_id = int(report["version_group_id"])
        parent_label = f"{report['report_number']} / {report['pipe_number']}"
        version_labels = {
            int(version["id"]): self._report_version_nav_label(version)
            for version in self.db.list_report_versions(version_group_id)
        }
        for row in range(self.report_nav.count()):
            item = self.report_nav.item(row)
            if int(item.data(ROLE_VERSION_GROUP_ID) or -1) != version_group_id:
                continue
            if item.data(ROLE_NAV_TYPE) == "report_group":
                item.setText("")
                item.setToolTip(parent_label)
                widget = self.report_nav.itemWidget(item)
                if isinstance(widget, ReportGroupNavWidget):
                    widget.set_label(parent_label)
                    widget.set_expanded(
                        version_group_id in self._expanded_report_version_groups
                    )
            elif int(item.data(ROLE_ID)) in version_labels:
                item.setText(f"     {version_labels[int(item.data(ROLE_ID))]}")

    def _fill_pair_items(self, table: QTableWidget, pairs: list[tuple[str, object]]) -> None:
        table.clearContents()
        rows = max(1, (len(pairs) + 1) // 2)
        table.setRowCount(rows)
        for idx, (label, value) in enumerate(pairs):
            row = idx // 2
            label_col = (idx % 2) * 2
            value_col = label_col + 1
            table.setItem(row, label_col, read_only_table_item(label, is_label=True))
            table.setItem(row, value_col, read_only_table_item(value, row_index=row))

    def _update_video_info_table(self) -> None:
        video = self.db.get_video(self.current_report_id) if self.current_report_id else None
        if video is None:
            pairs = [("영상파일", ""), ("영상길이", ""), ("녹화일", ""), ("주행방향", ""), ("깊이 OCR 영역", "")]
        else:
            roi = ""
            if None not in (video["depth_roi_x"], video["depth_roi_y"], video["depth_roi_w"], video["depth_roi_h"]):
                roi = f"{video['depth_roi_x']},{video['depth_roi_y']},{video['depth_roi_w']},{video['depth_roi_h']}"
            pairs = [
                ("영상파일", video["file_path"]),
                ("영상길이", video["duration"] or ""),
                ("녹화일", video["recorded_date"] or ""),
                ("주행방향", video["scan_direction"]),
                ("깊이 OCR 영역", roi),
            ]
        self._fill_pair_items(self.video_info_table, pairs)

    def _reset_report_workspace(self, *, flush_pending: bool = True) -> bool:
        if flush_pending and not self._flush_pending_report_autosave():
            self._restore_current_navigation_selection()
            return False
        self._set_report_controls_enabled(False)
        self.autosave_timer.stop()
        self.stop_playback()
        self._release_video()
        self.center_panel.setVisible(False)
        self.current_version_group_id = None
        self.current_video_id = None
        self.current_video_path = None
        self.current_video_meta = None
        self.current_frame = None
        self.current_frame_index = 0
        self.current_depth_roi = None
        self.pending_depth_roi = None
        self.is_selecting_depth_roi = False
        self._clear_stop_segments()
        self.video_status_label.setText("보고서를 선택하세요")
        self.video_label.clear()
        self._clear_capture_preview()
        self.timeline_slider.setValue(0)
        self.timeline_slider.setMaximum(1)
        self.timeline_slider.set_markers([])
        self.current_time_label.setText("00:00")
        self.total_time_label.setText("00:00")
        was_loading = self._loading_report
        self._loading_report = True
        try:
            for edit in self.report_inputs.values():
                edit.clear()
            for role_inputs in self.manhole_inputs.values():
                for widget in role_inputs.values():
                    clear_input_widget(widget)
        finally:
            self._loading_report = was_loading
        self._report_details_dirty = False
        self.video_info_table.clearContents()
        self.defect_table.setRowCount(0)
        self._refresh_unit_state_grades([])
        fit_table_height_to_contents(self.defect_table)
        self.update_summary()
        return True

    def _set_report_controls_enabled(self, enabled: bool) -> None:
        for widget in self.report_controls:
            widget.setEnabled(enabled)

    def _update_pipe_derived(self) -> None:
        try:
            length_m = parse_float(self.length_input.text())
            total_m = parse_float(self.total_drive_input.text())
        except ValueError:
            self.completion_label.setText("입력 오류")
            self.undriven_label.setText("")
            self.update_export_state()
            return
        completed, undriven = Database.compute_pipe_completion(length_m, total_m)
        self.completion_label.setText("완주" if completed else "미완주")
        self.undriven_label.setText("" if undriven is None else f"{undriven:.3f}")
        self.update_export_state()

    def _update_actual_direction_labels(self) -> None:
        up = input_widget_text(self.manhole_inputs["upstream"]["manhole_number"])
        down = input_widget_text(self.manhole_inputs["downstream"]["manhole_number"])
        self.start_direction_item.setText(f"{up} -> {down}")
        self.end_direction_item.setText(f"{down} -> {up}")

    def save_report_details(self) -> None:
        if self.current_report_id is None:
            return
        report_id = self.current_report_id
        try:
            report_payload = {
                field: self.report_inputs[field].text().strip() or None
                for field in REPORT_FIELDS
            }
            self.db.update_report(report_id, report_payload)
            for role in ("upstream", "downstream"):
                manhole_payload = {
                    field: input_widget_text(self.manhole_inputs[role][field]) or None
                    for field in MANHOLE_FIELDS
                }
                self.db.update_manhole(report_id, role, manhole_payload)
            self.db.update_pipe_information(
                report_id,
                parse_float(self.length_input.text()),
                parse_float(self.total_drive_input.text()),
            )
            self.db.update_actual_survey(
                report_id,
                {
                    "start_occurrence_point_m": parse_float(self.start_occurrence_input.text()),
                    "start_undriven_reason": self.start_reason_combo.currentText(),
                    "start_undriven_reason_detail": self.start_reason_detail_input.text().strip() or None,
                    "end_occurrence_point_m": parse_float(self.end_occurrence_input.text()),
                    "end_undriven_reason": self.end_reason_combo.currentText(),
                    "end_undriven_reason_detail": self.end_reason_detail_input.text().strip() or None,
                    "survey_content": self.survey_content_input.text().strip() or None,
                },
            )
        except ValueError as exc:
            QMessageBox.warning(self, "입력 오류", str(exc))
            return
        except Exception as exc:
            self.logger.exception("Failed to save report details")
            QMessageBox.critical(self, "저장 오류", str(exc))
            return
        self._report_details_dirty = False
        self.refresh_tree()
        self._select_tree_entity("report", report_id)
        if self.current_report_id != report_id:
            self.load_report(report_id)
        QMessageBox.information(self, "저장", "보고서 정보를 저장했습니다")

    def update_export_state(self) -> None:
        if not hasattr(self, "report_export_button"):
            return
        required_report = [
            "report_number",
            "pipe_number",
            "survey_date",
            "buried_years",
        ]
        report_ok = all(
            self.report_inputs.get(field) is not None
            and self.report_inputs[field].text().strip()
            for field in required_report
        )
        pipe_ok = bool(self.length_input.text().strip() and self.total_drive_input.text().strip())
        enabled = self.current_report_id is not None and report_ok and pipe_ok
        self.report_export_button.setEnabled(enabled)

    def load_report_video(self) -> None:
        self._release_video()
        self._reset_player_only()
        if self.current_report_id is None:
            return
        video = self.db.get_video(self.current_report_id)
        if video is None:
            self.video_status_label.setText("영상 없음")
            self.video_label.setText("영상 파일을 여기에 드래그해 등록하세요")
            self.video_overlay.hide()
            self._update_video_info_table()
            return
        self.current_video_id = int(video["id"])
        self.current_video_path = Path(video["file_path"])
        set_combo_text(self.video_direction_combo, video["scan_direction"], "순주행")
        if None in (video["depth_roi_x"], video["depth_roi_y"], video["depth_roi_w"], video["depth_roi_h"]):
            self.current_depth_roi = None
        else:
            self.current_depth_roi = (
                int(video["depth_roi_x"]),
                int(video["depth_roi_y"]),
                int(video["depth_roi_w"]),
                int(video["depth_roi_h"]),
            )
        self.pending_depth_roi = self.current_depth_roi
        self.video_status_label.setText(self.current_video_path.name)
        self._update_video_info_table()
        self.load_video()

    def add_or_replace_video(self) -> None:
        if self.current_report_id is None:
            QMessageBox.warning(self, "보고서 필요", "먼저 보고서를 선택하세요")
            return
        file_path_raw, _ = QFileDialog.getOpenFileName(
            self, "영상 선택", str(Path.home()), "영상 파일 (*.mp4 *.avi *.mov *.mkv)"
        )
        if not file_path_raw:
            return
        file_path = Path(file_path_raw)
        self._register_video_file(file_path, confirm_replace=True)

    def _register_video_file(
        self, file_path: Path, *, confirm_replace: bool
    ) -> bool:
        if self.current_report_id is None:
            QMessageBox.warning(self, "보고서 필요", "먼저 보고서를 선택하세요")
            return False
        if file_path.suffix.lower() not in VIDEO_FILE_EXTENSIONS:
            QMessageBox.warning(self, "영상 오류", "지원하지 않는 영상 형식입니다")
            return False
        existing = self.db.get_video(self.current_report_id)
        if confirm_replace and existing is not None:
            result = QMessageBox.warning(
                self,
                "영상 교체",
                "이미 등록된 영상이 있습니다. 영상만 교체하고 기존 결함/캡처 이미지는 유지합니다. 계속하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return False
        try:
            meta = VideoService.read_metadata(file_path)
            self.inspection.register_or_replace_video(
                self.current_report_id,
                file_path,
                None,
                meta.duration_seconds,
                self.video_direction_combo.currentText(),
            )
        except Exception as exc:
            self.logger.exception("Failed to register video")
            QMessageBox.critical(self, "영상 오류", str(exc))
            return False
        self._clear_stop_segments()
        self.load_report_video()
        self.refresh_defects()
        return True

    def save_video_info(self) -> None:
        if self.current_video_id is None:
            return
        self.db.update_video_scan_direction(
            self.current_video_id, self.video_direction_combo.currentText()
        )
        self._update_video_info_table()

    def set_depth_area(self) -> None:
        if self.current_video_id is None or self.current_frame is None:
            QMessageBox.warning(self, "깊이 OCR", "먼저 영상 프레임을 불러오세요")
            return
        self.stop_playback()
        self.is_selecting_depth_roi = True
        self.pending_depth_roi = None
        self.depth_drag_start = None
        self.depth_drag_end = None
        self.save_depth_roi_button.setVisible(True)
        self.save_depth_roi_button.setEnabled(False)
        self.set_depth_roi_button.setVisible(False)
        self.video_label.setCursor(Qt.CursorShape.CrossCursor)
        QMessageBox.information(self, "깊이 OCR", "영상에서 드래그로 깊이 영역을 선택하세요")

    def save_depth_area(self) -> None:
        if self.current_video_id is None or self.pending_depth_roi is None:
            return
        x, y, w, h = self.pending_depth_roi
        self.db.update_video_depth_roi(self.current_video_id, x, y, w, h)
        self.current_depth_roi = self.pending_depth_roi
        self._clear_stop_segments()
        self.is_selecting_depth_roi = False
        self.video_label.unsetCursor()
        self.save_depth_roi_button.setVisible(False)
        self.save_depth_roi_button.setEnabled(False)
        self.set_depth_roi_button.setVisible(True)
        self._refresh_video_display()
        self._update_video_info_table()
        self.refresh_defects()

    def load_video(self) -> None:
        self._release_video()
        if self.current_video_path is None:
            return
        self.cap = cv2.VideoCapture(str(self.current_video_path))
        if not self.cap.isOpened():
            self.cap = None
            self.video_overlay.hide()
            QMessageBox.warning(self, "영상 오류", "선택한 영상을 열 수 없습니다")
            return
        fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_ms = int((frame_count / fps) * 1000) if fps > 0 else 0
        self.current_video_meta = VideoMeta(
            duration_seconds=duration_ms / 1000.0,
            fps=fps,
            frame_count=frame_count,
            width=int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            height=int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
        )
        self.timeline_slider.setMaximum(max(1, duration_ms))
        self.total_time_label.setText(format_short_timestamp(duration_ms))
        self.video_overlay.show()
        self._position_video_overlay()
        self._seek_ms(0)

    def _release_video(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def _reset_player_only(self) -> None:
        self.stop_playback()
        self.current_frame = None
        self.current_frame_index = 0
        self.current_video_meta = None
        self.current_video_id = None
        self.current_video_path = None
        self.current_depth_roi = None
        self.pending_depth_roi = None
        self.is_selecting_depth_roi = False
        self.is_user_seeking = False
        self.depth_drag_start = None
        self.depth_drag_end = None
        self.video_label.unsetCursor()
        self.save_depth_roi_button.setVisible(False)
        self.save_depth_roi_button.setEnabled(False)
        self.set_depth_roi_button.setVisible(True)
        self.video_label.clear()
        self.video_overlay.hide()
        self._clear_capture_preview()
        self.current_time_label.setText("00:00")
        self.total_time_label.setText("00:00")
        self.timeline_slider.setValue(0)
        self.timeline_slider.setMaximum(1)

    def closeEvent(self, event) -> None:
        if not self._flush_pending_report_autosave():
            event.ignore()
            return
        self._release_video()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_sidebar_restore_handle()
        self._refresh_capture_preview_pixmap()
        self._position_video_overlay()

    def _frame_interval_ms(self) -> int:
        if self.current_video_meta is None or self.current_video_meta.fps <= 0:
            return 33
        return max(1, int((1000.0 / self.current_video_meta.fps) / self.playback_speed))

    def _speed_changed(self, text: str) -> None:
        self.playback_speed = float(text.lower().replace("x", ""))
        self._set_speed_button_text()
        if self.is_playing:
            self.play_timer.start(self._frame_interval_ms())

    def toggle_play(self) -> None:
        if self.cap is None:
            return
        if self.is_playing:
            self.stop_playback()
        else:
            self.is_playing = True
            self._set_player_button_icon(self.play_button, "pause")
            self.play_timer.start(self._frame_interval_ms())

    def stop_playback(self) -> None:
        self.is_playing = False
        self._set_player_button_icon(self.play_button, "play")
        self.play_timer.stop()

    def _play_tick(self) -> None:
        if self.cap is None:
            return
        ok, frame = self.cap.read()
        if not ok:
            self.stop_playback()
            return
        self.current_frame = frame
        self.current_frame_index = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        self._render_frame(frame)

    def _render_frame(self, frame) -> None:
        frame_to_draw = frame.copy()
        if self.current_depth_roi is not None:
            x, y, w, h = self.current_depth_roi
            cv2.rectangle(frame_to_draw, (x, y), (x + w, y + h), (0, 255, 255), 2)
        drag_preview_roi = self._display_drag_to_frame_roi()
        if drag_preview_roi is not None:
            x, y, w, h = drag_preview_roi
            cv2.rectangle(frame_to_draw, (x, y), (x + w, y + h), (255, 255, 0), 2)
        self.video_label.setPixmap(self._frame_to_pixmap(frame_to_draw))
        if self.current_video_meta and self.current_video_meta.fps > 0:
            timestamp_ms = int((self.current_frame_index / self.current_video_meta.fps) * 1000)
            self.current_time_label.setText(format_short_timestamp(timestamp_ms))
            if not self.is_user_seeking:
                self.timeline_slider.blockSignals(True)
                self.timeline_slider.setValue(min(timestamp_ms, self.timeline_slider.maximum()))
                self.timeline_slider.blockSignals(False)

    def _frame_to_pixmap(self, frame) -> QPixmap:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(image)

    def _set_capture_preview_pixmap(self, pixmap: QPixmap) -> None:
        if not hasattr(self, "capture_preview_label"):
            return
        self.capture_preview_source_pixmap = pixmap
        self._refresh_capture_preview_pixmap()

    def _refresh_capture_preview_pixmap(self) -> None:
        if (
            not hasattr(self, "capture_preview_label")
            or self.capture_preview_source_pixmap is None
        ):
            return
        self.capture_preview_label.setPixmap(self.capture_preview_source_pixmap)

    def _position_video_overlay(self) -> None:
        if not hasattr(self, "video_overlay"):
            return
        overlay_height = max(
            self.video_overlay.sizeHint().height(),
            int(self.video_label.height() * 0.20),
        )
        self.video_overlay.setGeometry(
            0,
            max(0, self.video_label.height() - overlay_height),
            self.video_label.width(),
            overlay_height,
        )
        self.video_overlay.raise_()

    def _set_capture_preview_from_frame(self, frame) -> None:
        self._set_capture_preview_pixmap(self._frame_to_pixmap(frame))

    def _pixmap_from_path(self, image_path: str) -> QPixmap | None:
        path = Path(image_path)
        if not path.exists():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        return pixmap

    def _set_capture_preview_from_path(self, image_path: str) -> bool:
        pixmap = self._pixmap_from_path(image_path)
        if pixmap is None:
            self._clear_capture_preview()
            return False
        self._set_capture_preview_pixmap(pixmap)
        return True

    def _set_video_display_from_path(self, image_path: str) -> bool:
        pixmap = self._pixmap_from_path(image_path)
        if pixmap is None:
            return False
        self.stop_playback()
        self.video_label.setPixmap(pixmap)
        self._position_video_overlay()
        return True

    def _clear_capture_preview(self) -> None:
        if not hasattr(self, "capture_preview_label"):
            return
        self.capture_preview_source_pixmap = None
        self.capture_preview_label.clear()
        self.capture_preview_label.setText("캡처된 프레임 없음")

    def _refresh_video_display(self) -> None:
        if self.current_frame is not None:
            self._render_frame(self.current_frame)

    def _display_drag_to_frame_roi(self) -> Optional[tuple[int, int, int, int]]:
        if self.current_frame is None or self.depth_drag_start is None or self.depth_drag_end is None:
            return None
        start = self._label_point_to_frame_point(self.depth_drag_start)
        end = self._label_point_to_frame_point(self.depth_drag_end)
        if start is None or end is None:
            return None
        x1, y1 = min(start[0], end[0]), min(start[1], end[1])
        x2, y2 = max(start[0], end[0]), max(start[1], end[1])
        if x2 - x1 < 2 or y2 - y1 < 2:
            return None
        return x1, y1, x2 - x1, y2 - y1

    def _label_point_to_frame_point(self, point: QPoint) -> Optional[tuple[int, int]]:
        if self.current_frame is None:
            return None
        frame_h, frame_w = self.current_frame.shape[:2]
        label_w, label_h = max(1, self.video_label.width()), max(1, self.video_label.height())
        scale = min(label_w / frame_w, label_h / frame_h)
        draw_w, draw_h = int(frame_w * scale), int(frame_h * scale)
        offset_x, offset_y = (label_w - draw_w) // 2, (label_h - draw_h) // 2
        px, py = int(point.x()), int(point.y())
        if px < offset_x or py < offset_y or px > offset_x + draw_w or py > offset_y + draw_h:
            return None
        frame_x = int((px - offset_x) * frame_w / max(1, draw_w))
        frame_y = int((py - offset_y) * frame_h / max(1, draw_h))
        return max(0, min(frame_w - 1, frame_x)), max(0, min(frame_h - 1, frame_y))

    def _on_slider_pressed(self) -> None:
        self.is_user_seeking = True

    def _seek_ms(self, target_ms: int) -> None:
        if self.cap is None or self.current_video_meta is None or self.current_video_meta.fps <= 0:
            return
        target_ms = max(0, min(target_ms, self.timeline_slider.maximum()))
        target_frame = int((target_ms / 1000.0) * self.current_video_meta.fps)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ok, frame = self.cap.read()
        if ok:
            self.current_frame = frame
            self.current_frame_index = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
            self._render_frame(frame)

    def _seek_from_slider(self) -> None:
        self.is_user_seeking = False
        self._seek_ms(self.timeline_slider.value())

    def _seek_to_marker(self, timestamp_ms: int) -> None:
        self._seek_ms(timestamp_ms)

    def step_frame(self, delta: int) -> None:
        if self.current_video_meta is None:
            return
        target = max(0, self.current_frame_index + delta)
        if self.current_video_meta.fps <= 0:
            return
        self._seek_ms(int((target / self.current_video_meta.fps) * 1000))

    def jump_seconds(self, seconds: int) -> None:
        if self.current_video_meta is None:
            return
        self._seek_ms(self.timeline_slider.value() + (seconds * 1000))

    def capture_frame(self) -> None:
        if self.current_frame is None:
            QMessageBox.warning(self, "캡처", "현재 프레임이 없습니다")
            return
        self.stop_playback()
        self._set_pending_capture_from_current_frame(self.timeline_slider.value())
        self.video_label.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_pending_capture_from_current_frame(self, timestamp_ms: int) -> None:
        if self.current_frame is None:
            return
        self.pending_capture_frame = self.current_frame.copy()
        self.pending_capture_timestamp_ms = timestamp_ms
        self._set_capture_preview_from_frame(self.pending_capture_frame)
        if self.current_video_id is not None:
            try:
                distance = self.inspection.read_distance_for_frame(
                    self.current_video_id, self.pending_capture_frame
                )
                if distance is not None:
                    self.distance_input.setText(f"{distance:.3f}")
            except Exception:
                self.logger.exception("Failed distance OCR on capture")

    def set_grade(self, grade: str) -> None:
        if not self.grade_combo.isEnabled():
            return
        if self.grade_combo.findText(grade) >= 0:
            self.grade_combo.setCurrentText(grade)

    def save_defect(self) -> None:
        if self.editing_defect_id is not None:
            self._save_defect_edit()
            return
        if self.current_report_id is None or self.current_video_id is None or self.current_video_path is None:
            QMessageBox.warning(self, "결함", "먼저 보고서와 영상을 선택하세요")
            return
        if self.pending_capture_frame is None or self.pending_capture_timestamp_ms is None:
            QMessageBox.warning(self, "결함", "먼저 프레임을 캡처하세요")
            return
        distance_m = self._validated_defect_distance()
        if distance_m is None:
            return
        selection = self._validated_defect_selection()
        if selection is None:
            return
        condition_item, defect_item, grade = selection
        try:
            self.inspection.capture_and_save_defect(
                report_id=self.current_report_id,
                video_id=self.current_video_id,
                video_file=self.current_video_path,
                frame=self.pending_capture_frame,
                timestamp_ms=self.pending_capture_timestamp_ms,
                drive_direction=self.defect_drive_direction_combo.currentText(),
                distance_m=distance_m,
                item_category=self.item_category_combo.currentText(),
                condition_item=condition_item,
                defect_item=defect_item,
                grade=grade,
                quadrant=self.quadrant_combo.currentText(),
                manhole_defect_depth_m=parse_float(self.manhole_defect_depth_input.text()),
                memo=self.memo_input.text().strip() or None,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "입력 오류", str(exc))
            return
        except Exception as exc:
            self.logger.exception("Failed to save defect")
            QMessageBox.critical(self, "결함 저장 오류", str(exc))
            return
        self.pending_capture_frame = None
        self.pending_capture_timestamp_ms = None
        self._clear_defect_form()
        self._clear_capture_preview()
        self.refresh_defects()
        self.update_summary()

    def _validated_defect_distance(self) -> float | None:
        try:
            return parse_required_float(self.distance_input.text(), "거리(m)")
        except ValueError as exc:
            QMessageBox.warning(self, "입력 오류", str(exc))
            return None

    def _save_defect_edit(self) -> None:
        if self.editing_defect_id is None:
            return
        distance_m = self._validated_defect_distance()
        if distance_m is None:
            return
        selection = self._validated_defect_selection()
        if selection is None:
            return
        condition_item, defect_item, grade = selection
        try:
            self.db.update_defect(
                self.editing_defect_id,
                {
                    "drive_direction": self.defect_drive_direction_combo.currentText(),
                    "distance_m": distance_m,
                    "item_category": self.item_category_combo.currentText(),
                    "condition_item": condition_item,
                    "defect_item": defect_item,
                    "grade": grade,
                    "quadrant": self.quadrant_combo.currentText(),
                    "manhole_defect_depth_m": parse_float(self.manhole_defect_depth_input.text()),
                    "memo": self.memo_input.text().strip() or None,
                },
            )
        except ValueError as exc:
            QMessageBox.warning(self, "입력 오류", str(exc))
            return
        self.cancel_defect_edit()
        self.refresh_defects()
        self.update_summary()

    def _clear_defect_form(self) -> None:
        self.distance_input.clear()
        self.manhole_defect_depth_input.clear()
        self.memo_input.clear()
        if self.editing_defect_id is None:
            self._clear_capture_preview()
        self._update_defect_grade_options()

    def cancel_defect_edit(self) -> None:
        self.editing_defect_id = None
        self.save_defect_button.setText("결함 저장 (Enter)")
        self._clear_defect_form()

    def refresh_defects(self) -> None:
        if self.current_report_id is None:
            self.defect_table.setRowCount(0)
            self._refresh_unit_state_grades([])
            fit_table_height_to_contents(self.defect_table)
            self.timeline_slider.set_markers([])
            self._refresh_stop_segment_list()
            return
        rows = self.db.list_defects(self.current_report_id)
        self._refresh_unit_state_grades(rows)
        if self.show_stop_only_checkbox.isChecked():
            self.defect_table.setRowCount(len(self.stop_segments))
            for idx, seg in enumerate(self.stop_segments):
                start_ms = int(seg["start_time"] * 1000)
                distance = seg.get("distance_m")
                payload = [
                    "",
                    str(start_ms),
                    idx + 1,
                    format_short_timestamp(start_ms),
                    "",
                    "" if distance is None else f"{float(distance):.1f}",
                    "",
                    "의심구간",
                    "",
                    "S",
                    "",
                    "",
                    "",
                    f"{seg['duration']:.2f}s",
                    "",
                ]
                for col, value in enumerate(payload):
                    self.defect_table.setItem(idx, col, read_only_table_item(value, row_index=idx))
        else:
            self.defect_table.setRowCount(len(rows))
            for idx, row in enumerate(rows):
                score = defect_score(
                    row["item_category"] or "",
                    row["defect_item"] or "",
                    row["grade"] or "",
                )
                payload = [
                    row["id"],
                    row["timestamp_ms"],
                    idx + 1,
                    format_short_timestamp(int(row["timestamp_ms"])),
                    row["drive_direction"],
                    "" if row["distance_m"] is None else f"{float(row['distance_m']):.3f}",
                    row["item_category"] or "",
                    row["condition_item"] or "",
                    row["defect_item"] or "",
                    row["grade"] or "",
                    row["quadrant"] or "",
                    "" if row["manhole_defect_depth_m"] is None else f"{float(row['manhole_defect_depth_m']):.3f}",
                    "" if score is None else score,
                    row["memo"] or "",
                    row["image_path"],
                ]
                for col, value in enumerate(payload):
                    self.defect_table.setItem(idx, col, read_only_table_item(value, row_index=idx))
        fit_table_height_to_contents(self.defect_table)
        self._refresh_timeline_markers(rows)
        self._refresh_stop_segment_list()

    def _refresh_unit_state_grades(self, rows) -> None:
        if not hasattr(self, "unit_state_grade_table"):
            return
        summary = compute_pipe_state_grades(rows)
        self.state_grade_summary_label.setText(
            (
                f"구조등급 {format_state_value(summary.structural_grade) or '-'} / "
                f"운영등급 {format_state_value(summary.operational_grade) or '-'}"
            )
        )
        self.unit_state_grade_table.setRowCount(len(summary.sections))
        for row_index, section in enumerate(summary.sections):
            time_range = format_state_time_range(
                section.start_timestamp_ms, section.end_timestamp_ms
            )
            section_label = format_state_section_label(section)
            section_info = "\n".join(
                part for part in (time_range, section_label) if part
            )
            distance_range = (
                f"{format_state_distance(section.start_distance_m)}~"
                f"{format_state_distance(section.end_distance_m)}"
            )
            values = [
                section.index,
                section_info,
                section.drive_direction,
                distance_range,
                section.structural_score,
                section.structural_grade,
                section.operational_score,
                section.operational_grade,
                section.defect_count,
            ]
            for col, value in enumerate(values):
                self.unit_state_grade_table.setItem(
                    row_index,
                    col,
                    read_only_table_item(value, row_index=row_index),
                )
        fit_table_height_to_contents(self.unit_state_grade_table)

    def _clear_stop_segments(self) -> None:
        self.stop_segments = []
        self._refresh_stop_segment_list()

    def _refresh_stop_segment_list(self) -> None:
        if not hasattr(self, "stop_segment_list"):
            return
        self.stop_segment_list.clear()
        self.stop_segment_count_label.setText(str(len(self.stop_segments)))
        self.detect_stop_button.setText("분석")
        for idx, seg in enumerate(self.stop_segments, start=1):
            start_ms = int(float(seg["start_time"]) * 1000)
            end_ms = int(float(seg["end_time"]) * 1000)
            duration = float(seg["duration"])
            distance = seg.get("distance_m")
            distance_text = "-" if distance is None else f"{float(distance):.1f} m"
            candidates = seg.get("candidates", [])
            candidate_count = len(candidates) if isinstance(candidates, list) else 0
            analysis_seconds = self._stop_candidate_value(seg, "analysis_seconds", -1.0)
            analysis_text = (
                "" if analysis_seconds < 0 else f" · 분석 {analysis_seconds:.1f}s"
            )
            item = QListWidgetItem(
                f"{idx:02d}  {format_short_timestamp(start_ms)}-{format_short_timestamp(end_ms)}\n"
                f"     {distance_text} · {duration:.1f}s · 후보 {candidate_count}{analysis_text}"
            )
            item.setToolTip(self._stop_segment_debug_tooltip(idx, seg))
            item.setSizeHint(QSize(0, 54 if analysis_seconds >= 0 else 46))
            item.setData(ROLE_STOP_ITEM_TYPE, "segment")
            item.setData(ROLE_STOP_TIMESTAMP_MS, start_ms)
            self.stop_segment_list.addItem(item)
            if not isinstance(candidates, list):
                continue
            for candidate_idx, candidate in enumerate(candidates, start=1):
                timestamp = float(candidate.get("timestamp", 0.0))
                timestamp_ms = int(float(candidate.get("timestamp_ms", timestamp * 1000)))
                candidate_item = QListWidgetItem(
                    self._stop_candidate_debug_summary(
                        candidate_idx,
                        timestamp_ms,
                        candidate,
                    )
                )
                candidate_item.setToolTip(
                    self._stop_candidate_debug_tooltip(candidate_idx, timestamp_ms, candidate)
                )
                candidate_item.setSizeHint(QSize(0, 74))
                candidate_item.setData(ROLE_STOP_ITEM_TYPE, "candidate")
                candidate_item.setData(ROLE_STOP_TIMESTAMP_MS, timestamp_ms)
                self.stop_segment_list.addItem(candidate_item)

    def _stop_candidate_value(
        self, candidate: dict[str, object], key: str, default: float = 0.0
    ) -> float:
        value = candidate.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _stop_segment_debug_tooltip(
        self, segment_idx: int, segment: dict[str, object]
    ) -> str:
        start_ms = int(float(segment.get("start_time", 0.0)) * 1000)
        end_ms = int(float(segment.get("end_time", 0.0)) * 1000)
        distance = segment.get("distance_m")
        distance_text = "-" if distance is None else f"{float(distance):.1f} m"
        candidates = segment.get("candidates", [])
        candidate_count = len(candidates) if isinstance(candidates, list) else 0
        lines = [
            f"의심구간 {segment_idx}",
            f"시간: {format_short_timestamp(start_ms)}-{format_short_timestamp(end_ms)}",
            f"거리: {distance_text}",
            f"후보: {candidate_count}개",
        ]
        analysis_seconds = self._stop_candidate_value(segment, "analysis_seconds", -1.0)
        if analysis_seconds >= 0:
            lines.extend(
                [
                    "",
                    "후보 분석 시간",
                    f"총: {analysis_seconds:.4f}s",
                    f"프레임 읽기/전처리: {self._stop_candidate_value(segment, 'candidate_read_seconds'):.4f}s",
                    f"후보 점수 계산: {self._stop_candidate_value(segment, 'candidate_select_seconds'):.4f}s",
                    f"샘플 수: {int(self._stop_candidate_value(segment, 'candidate_sample_count'))}",
                ]
            )
        error = segment.get("analysis_error")
        if error:
            lines.extend(["", f"후보 분석 오류: {error}"])
        return "\n".join(lines)

    def _stop_candidate_debug_summary(
        self,
        candidate_idx: int,
        timestamp_ms: int,
        candidate: dict[str, object],
    ) -> str:
        confidence = self._stop_candidate_value(candidate, "confidence")
        score = self._stop_candidate_value(candidate, "score")
        stability = self._stop_candidate_value(candidate, "stability_score")
        pre_rotation = self._stop_candidate_value(candidate, "pre_rotation_strength")
        post_rotation = self._stop_candidate_value(candidate, "post_rotation_strength")
        post_translation = self._stop_candidate_value(
            candidate, "post_translation_strength"
        )
        sharpness = self._stop_candidate_value(candidate, "sharpness_score")
        duration = self._stop_candidate_value(candidate, "duration_score")
        departure = self._stop_candidate_value(candidate, "departure_penalty")
        edge = self._stop_candidate_value(candidate, "edge_penalty")
        return (
            f"   후보 {candidate_idx}  {format_short_timestamp(timestamp_ms)}"
            f"  {confidence * 100:.0f}% · 점수 {score:.2f}\n"
            f"      안정 {stability:.2f} · 전회 {pre_rotation:.2f}"
            f" · 후회 {post_rotation:.2f} · 후전 {post_translation:.2f}\n"
            f"      선명 {sharpness:.2f} · 지속 {duration:.2f}"
            f" · 출발감 {departure:.2f} · 경계 {edge:.2f}"
        )

    def _stop_candidate_debug_tooltip(
        self,
        candidate_idx: int,
        timestamp_ms: int,
        candidate: dict[str, object],
    ) -> str:
        lines = [
            f"후보 {candidate_idx}  {format_short_timestamp(timestamp_ms)}",
            f"총점: {self._stop_candidate_value(candidate, 'score'):.4f}",
            f"신뢰도: {self._stop_candidate_value(candidate, 'confidence'):.4f}",
            "",
            "정규화 구성요소",
            f"안정: {self._stop_candidate_value(candidate, 'stability_score'):.4f}",
            f"전회전: {self._stop_candidate_value(candidate, 'pre_rotation_strength'):.4f}",
            f"후회전: {self._stop_candidate_value(candidate, 'post_rotation_strength'):.4f}",
            f"후전진: {self._stop_candidate_value(candidate, 'post_translation_strength'):.4f}",
            f"선명: {self._stop_candidate_value(candidate, 'sharpness_score'):.4f}",
            f"지속: {self._stop_candidate_value(candidate, 'duration_score'):.4f}",
            f"출발감점: {self._stop_candidate_value(candidate, 'departure_penalty'):.4f}",
            f"경계감점: {self._stop_candidate_value(candidate, 'edge_penalty'):.4f}",
            "",
            "원시값",
            f"평균 motion: {self._stop_candidate_value(candidate, 'motion_score'):.4f}",
            f"smoothed 평균 motion: {self._stop_candidate_value(candidate, 'smoothed_motion_score'):.4f}",
            f"선택 프레임 smoothed motion: {self._stop_candidate_value(candidate, 'best_smoothed_motion'):.4f}",
            f"전회전 raw: {self._stop_candidate_value(candidate, 'pre_rotation_score'):.4f}",
            f"전회전 peak: {self._stop_candidate_value(candidate, 'pre_rotation_peak'):.4f}",
            f"전회전 energy: {self._stop_candidate_value(candidate, 'pre_rotation_energy'):.4f}",
            f"전회전 energy score: {self._stop_candidate_value(candidate, 'pre_rotation_energy_score'):.4f}",
            f"후회전 raw: {self._stop_candidate_value(candidate, 'post_rotation_score'):.4f}",
            f"후회전 peak: {self._stop_candidate_value(candidate, 'post_rotation_peak'):.4f}",
            f"후회전 energy: {self._stop_candidate_value(candidate, 'post_rotation_energy'):.4f}",
            f"후회전 energy score: {self._stop_candidate_value(candidate, 'post_rotation_energy_score'):.4f}",
            f"후전진 raw: {self._stop_candidate_value(candidate, 'post_translation_score'):.4f}",
            f"후전진 peak: {self._stop_candidate_value(candidate, 'post_translation_peak'):.4f}",
            f"후전진 energy: {self._stop_candidate_value(candidate, 'post_translation_energy'):.4f}",
            f"후전진 energy score: {self._stop_candidate_value(candidate, 'post_translation_energy_score'):.4f}",
            f"선명 raw: {self._stop_candidate_value(candidate, 'sharpness'):.4f}",
            f"안정 threshold: {self._stop_candidate_value(candidate, 'stable_threshold'):.4f}",
            f"이동 threshold: {self._stop_candidate_value(candidate, 'moving_threshold'):.4f}",
            "",
            "총점 기여도",
        ]
        score_components = candidate.get("score_components")
        if isinstance(score_components, dict):
            for key, label in (
                ("duration", "지속"),
                ("pre_rotation", "전회전"),
                ("post_rotation", "후회전"),
                ("sharpness", "선명"),
                ("stability", "안정"),
                ("departure_penalty", "출발감점"),
                ("edge_penalty", "경계감점"),
            ):
                lines.append(
                    f"{label}: {self._stop_candidate_value(score_components, key):+.4f}"
                )
        else:
            lines.append("(없음)")
        lines.extend(["", "신뢰도 기여도"])
        confidence_components = candidate.get("confidence_components")
        if isinstance(confidence_components, dict):
            for key, label in (
                ("stability", "안정"),
                ("pre_rotation", "전회전"),
                ("post_rotation", "후회전"),
                ("sharpness", "선명"),
                ("duration", "지속"),
                ("departure_penalty", "출발감점"),
                ("edge_penalty", "경계감점"),
            ):
                lines.append(
                    f"{label}: {self._stop_candidate_value(confidence_components, key):+.4f}"
                )
        else:
            lines.append("(없음)")
        return "\n".join(lines)

    def _seek_to_stop_segment_item(self, item: QListWidgetItem) -> None:
        timestamp_ms = item.data(ROLE_STOP_TIMESTAMP_MS)
        if timestamp_ms is None:
            return
        if item.data(ROLE_STOP_ITEM_TYPE) == "candidate":
            self._preview_stop_frame_candidate(int(timestamp_ms))
        else:
            self._seek_ms(int(timestamp_ms))

    def _preview_stop_frame_candidate(self, timestamp_ms: int) -> None:
        self.stop_playback()
        self._seek_ms(timestamp_ms)
        if self.current_frame is None:
            return
        self._set_pending_capture_from_current_frame(timestamp_ms)

    def _refresh_timeline_markers(self, defect_rows) -> None:
        markers: list[tuple[int, str]] = []
        if not self.show_stop_only_checkbox.isChecked():
            for row in defect_rows:
                markers.append((int(row["timestamp_ms"]), row["grade"] or ""))
        self.timeline_slider.set_markers(markers)

    def update_summary(self) -> None:
        return

    def selected_defect_id(self) -> Optional[int]:
        row = self.defect_table.currentRow()
        if row < 0:
            return None
        cell = self.defect_table.item(row, 0)
        if cell is None:
            return None
        try:
            return int(cell.text())
        except ValueError:
            return None

    def _edit_defect_from_table(self, index) -> None:
        if index.isValid():
            self.defect_table.setCurrentCell(index.row(), index.column())
        self.edit_selected_defect()

    def _jump_to_selected_defect(self) -> None:
        row = self.defect_table.currentRow()
        if row < 0:
            return
        timestamp_item = self.defect_table.item(row, 1)
        if timestamp_item is None:
            return
        self._seek_ms(int(timestamp_item.text()))

    def edit_selected_defect(self) -> None:
        defect_id = self.selected_defect_id()
        if defect_id is None:
            return
        defect = self.db.get_defect(defect_id)
        if defect is None:
            return
        self.editing_defect_id = defect_id
        set_combo_text(self.defect_drive_direction_combo, defect["drive_direction"], "순주행")
        self.distance_input.setText("" if defect["distance_m"] is None else str(defect["distance_m"]))
        set_combo_text(self.item_category_combo, defect["item_category"], ITEM_CATEGORIES[0])
        self._refresh_defect_taxonomy_controls(
            preferred_condition=defect["condition_item"],
            preferred_defect=defect["defect_item"],
            preferred_grade=defect["grade"],
        )
        set_combo_text(self.quadrant_combo, defect["quadrant"], QUADRANTS[0])
        self.manhole_defect_depth_input.setText(
            "" if defect["manhole_defect_depth_m"] is None else str(defect["manhole_defect_depth_m"])
        )
        self.memo_input.setText(defect["memo"] or "")
        self._set_capture_preview_from_path(defect["image_path"])
        self._set_video_display_from_path(defect["image_path"])
        self.save_defect_button.setText("결함 수정 저장")

    def delete_selected_defect(self) -> None:
        defect_id = self.selected_defect_id()
        if defect_id is None:
            return
        if QMessageBox.question(self, "확인", "선택한 결함을 삭제하시겠습니까?") != QMessageBox.StandardButton.Yes:
            return
        self.inspection.delete_defect_with_image(defect_id)
        self.refresh_defects()
        self.update_summary()

    def detect_stop_segments(self) -> None:
        if self.stop_analysis_thread is not None:
            QMessageBox.information(self, "의심구간", "이미 의심구간 분석이 진행 중입니다.")
            return
        if self.current_video_path is None:
            QMessageBox.warning(self, "의심구간", "먼저 영상을 선택하세요")
            return
        if self.current_depth_roi is None:
            QMessageBox.warning(self, "의심구간", "먼저 깊이 영역을 저장하세요")
            return
        self.stop_playback()

        self.detect_stop_button.setEnabled(False)
        self.detect_stop_button.setText("분석중")
        self._set_stop_analysis_status("의심구간 분석 준비")
        if hasattr(self, "stop_analysis_progress_bar"):
            self.stop_analysis_progress_bar.setValue(0)
            self.stop_analysis_progress_bar.show()

        thread = QThread(self)
        worker = StopAnalysisWorker(str(self.current_video_path), self.current_depth_roi)
        worker.moveToThread(thread)
        self.stop_analysis_thread = thread
        self.stop_analysis_worker = worker

        thread.started.connect(worker.run)
        worker.progress.connect(self._update_stop_analysis_progress)
        worker.finished.connect(self._handle_stop_analysis_finished)
        worker.failed.connect(self._handle_stop_analysis_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_stop_analysis_thread)
        thread.start()

    def _update_stop_analysis_progress(self, value: int, message: str) -> None:
        progress_value = max(0, min(100, int(value)))
        self._set_stop_analysis_status(f"{message} · {progress_value}%")
        if hasattr(self, "stop_analysis_progress_bar"):
            self.stop_analysis_progress_bar.setValue(progress_value)
            self.stop_analysis_progress_bar.show()

    def _handle_stop_analysis_finished(
        self, stop_segments: object, benchmark: object
    ) -> None:
        self.stop_segments = list(stop_segments) if isinstance(stop_segments, list) else []
        self.stop_analysis_benchmark = (
            dict(benchmark) if isinstance(benchmark, dict) else {}
        )
        self.refresh_defects()
        self._set_stop_analysis_status(self._format_stop_analysis_benchmark(), show=True)
        self._finish_stop_analysis_progress()

        candidate_count = sum(
            len(seg.get("candidates", []))
            for seg in self.stop_segments
            if isinstance(seg.get("candidates", []), list)
        )
        candidate_error = self.stop_analysis_benchmark.get("candidate_error")
        if candidate_error is not None:
            QMessageBox.warning(
                self,
                "의심구간",
                "의심구간 "
                f"{len(self.stop_segments)}개를 탐지했지만 후보 프레임 분석은 실패했습니다.\n"
                f"{candidate_error}\n\n{self._format_stop_analysis_benchmark()}",
            )
            return
        QMessageBox.information(
            self,
            "의심구간",
            "의심구간 "
            f"{len(self.stop_segments)}개와 후보 프레임 {candidate_count}개를 탐지했습니다.\n\n"
            f"{self._format_stop_analysis_benchmark()}",
        )

    def _handle_stop_analysis_failed(self, error_message: str) -> None:
        self.logger.error("Stop segment detection failed: %s", error_message)
        self._set_stop_analysis_status(f"분석 실패: {error_message}", show=True)
        self._finish_stop_analysis_progress()
        QMessageBox.critical(self, "의심구간 오류", error_message)

    def _finish_stop_analysis_progress(self) -> None:
        self.detect_stop_button.setEnabled(True)
        self.detect_stop_button.setText("분석")
        if hasattr(self, "stop_analysis_progress_bar"):
            self.stop_analysis_progress_bar.setValue(100)
            self.stop_analysis_progress_bar.hide()

    def _clear_stop_analysis_thread(self) -> None:
        self.stop_analysis_thread = None
        self.stop_analysis_worker = None

    def _set_stop_analysis_status(self, text: str, *, show: bool = True) -> None:
        if not hasattr(self, "stop_analysis_status_label"):
            return
        self.stop_analysis_status_label.setText(text)
        self.stop_analysis_status_label.setVisible(show and bool(text))

    def _format_stop_analysis_benchmark(self) -> str:
        total_seconds = float(self.stop_analysis_benchmark.get("total_seconds") or 0.0)
        depth_seconds = float(self.stop_analysis_benchmark.get("depth_seconds") or 0.0)
        candidate_seconds = float(
            self.stop_analysis_benchmark.get("candidate_seconds") or 0.0
        )
        lines = [
            "소요시간",
            f"총: {total_seconds:.2f}s",
            f"거리 OCR: {depth_seconds:.2f}s",
            f"후보 프레임: {candidate_seconds:.2f}s",
        ]
        worker_count = int(self.stop_analysis_benchmark.get("candidate_workers") or 0)
        if worker_count > 0:
            segment_sum = float(
                self.stop_analysis_benchmark.get("candidate_segment_seconds_sum")
                or 0.0
            )
            slowest = float(
                self.stop_analysis_benchmark.get("candidate_slowest_seconds")
                or 0.0
            )
            fastest = float(
                self.stop_analysis_benchmark.get("candidate_fastest_seconds")
                or 0.0
            )
            lines.extend(
                [
                    f"후보 워커: {worker_count}개",
                    f"구간별 시간 합계: {segment_sum:.2f}s",
                    f"가장 느린 구간: {slowest:.2f}s",
                    f"가장 빠른 구간: {fastest:.2f}s",
                ]
            )
        timed_segments = [
            (idx, seg)
            for idx, seg in enumerate(self.stop_segments, start=1)
            if self._stop_candidate_value(seg, "analysis_seconds", -1.0) >= 0
        ]
        if timed_segments:
            lines.extend(["", "느린 의심구간"])
            for idx, seg in sorted(
                timed_segments,
                key=lambda item: self._stop_candidate_value(
                    item[1], "analysis_seconds", 0.0
                ),
                reverse=True,
            )[:5]:
                start_ms = int(float(seg.get("start_time", 0.0)) * 1000)
                end_ms = int(float(seg.get("end_time", 0.0)) * 1000)
                lines.append(
                    f"{idx:02d} {format_short_timestamp(start_ms)}-"
                    f"{format_short_timestamp(end_ms)}: "
                    f"{self._stop_candidate_value(seg, 'analysis_seconds'):.2f}s"
                )
        return "\n".join(lines)

    def _excel_report_path_for_context(self, context, output_dir: Path | None = None) -> Path:
        default_path = self.inspection.storage.excel_report_path(
            context["project_name"],
            context["business_code"],
            context["business_name"],
            context["report_number"],
            context["pipe_number"],
            context["version_name"],
        )
        if output_dir is None:
            return default_path
        return output_dir / default_path.name

    def _default_report_output_dir(self, report_id: int) -> Path | None:
        context = self.db.get_report_context(report_id)
        if context is None:
            return None
        return self.inspection.storage.pdf_report_dir(
            context["project_name"],
            context["business_code"],
            context["business_name"],
            context["report_number"],
            context["pipe_number"],
            context["version_name"],
        )

    def _selected_output_dir(self, raw_path: str) -> Path | None:
        if not raw_path.strip():
            QMessageBox.warning(self, "보고서 생성", "생성 경로를 입력하세요")
            return None
        output_dir = Path(raw_path).expanduser()
        if output_dir.exists() and not output_dir.is_dir():
            QMessageBox.warning(self, "보고서 생성", "생성 경로가 폴더가 아닙니다")
            return None
        return output_dir

    def _open_generated_file(self, file_path: str | Path) -> None:
        path = Path(file_path).resolve()
        if not path.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def generate_excel_report(self, output_dir: Path | None = None) -> None:
        if self.current_report_id is None:
            return
        self.save_report_details_without_message()
        context = self.db.get_report_context(self.current_report_id)
        if context is None:
            return
        output_path = self._excel_report_path_for_context(context, output_dir)
        if output_path.exists():
            overwrite = QMessageBox.question(
                self, "덮어쓰기 확인", f"{output_path.name} 파일이 이미 있습니다. 덮어쓰시겠습니까?"
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                return
        try:
            report_path = self.inspection.generate_excel_report(
                self.current_report_id, report_path=output_path
            )
        except Exception as exc:
            self.logger.exception("Excel report generation failed")
            QMessageBox.critical(self, "보고서 오류", str(exc))
            return
        QMessageBox.information(self, "보고서", f"보고서가 생성되었습니다:\n{report_path}")
        self._open_generated_file(report_path)

    def open_report_export_dialog(self) -> None:
        if self.current_report_id is None:
            return
        report_options = self._pdf_report_options()
        if not report_options:
            QMessageBox.warning(self, "보고서 생성", "선택할 수 있는 보고서가 없습니다")
            return
        default_output_dir = self._default_report_output_dir(self.current_report_id)
        context = self.db.get_report_context(self.current_report_id)
        if default_output_dir is None or context is None:
            return
        excel_filename = self._excel_report_path_for_context(context).name
        dialog = ReportExportDialog(
            report_options,
            self.current_report_id,
            str(default_output_dir),
            excel_filename,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        output_dir = self._selected_output_dir(dialog.output_dir())
        if output_dir is None:
            return
        if dialog.selected_export_kind() == "excel":
            self.generate_excel_report(output_dir=output_dir)
            return

        report_type = dialog.pdf_report_type()
        if report_type == "comparison":
            before_report_id = dialog.before_report_id()
            after_report_id = dialog.after_report_id()
            if before_report_id == after_report_id:
                QMessageBox.warning(
                    self,
                    "PDF 보고서",
                    "비교보고서는 보수전과 보수후 보고서를 서로 다르게 선택해야 합니다.",
                )
                return
            self.generate_visual_pdf_report(
                report_type=report_type,
                before_report_id=before_report_id,
                after_report_id=after_report_id,
                output_dir=output_dir,
            )
            return

        self.generate_visual_pdf_report(
            report_type=report_type,
            before_report_id=self.current_report_id,
            after_report_id=None,
            output_dir=output_dir,
        )

    def save_report_details_without_message(self) -> None:
        if self.current_report_id is None:
            return
        report_payload = {
            field: self.report_inputs[field].text().strip() or None
            for field in REPORT_FIELDS
        }
        self.db.update_report(self.current_report_id, report_payload)
        for role in ("upstream", "downstream"):
            self.db.update_manhole(
                self.current_report_id,
                role,
                {
                    field: input_widget_text(self.manhole_inputs[role][field]) or None
                    for field in MANHOLE_FIELDS
                },
            )
        self.db.update_pipe_information(
            self.current_report_id,
            parse_float(self.length_input.text()),
            parse_float(self.total_drive_input.text()),
        )
        self.db.update_actual_survey(
            self.current_report_id,
            {
                "start_occurrence_point_m": parse_float(self.start_occurrence_input.text()),
                "start_undriven_reason": self.start_reason_combo.currentText(),
                "start_undriven_reason_detail": self.start_reason_detail_input.text().strip() or None,
                "end_occurrence_point_m": parse_float(self.end_occurrence_input.text()),
                "end_undriven_reason": self.end_reason_combo.currentText(),
                "end_undriven_reason_detail": self.end_reason_detail_input.text().strip() or None,
                "survey_content": self.survey_content_input.text().strip() or None,
            },
        )
        self._report_details_dirty = False

    def _resolve_default_pipe_png_path(self) -> Optional[str]:
        cwd_pipe = Path.cwd() / "pipe.png"
        if cwd_pipe.exists() and cwd_pipe.is_file():
            return str(cwd_pipe)
        workspace_pipe = self.inspection.storage.workspace_root / "pipe.png"
        if workspace_pipe.exists() and workspace_pipe.is_file():
            return str(workspace_pipe)
        return None

    def _pdf_report_options(self) -> list[tuple[int, str]]:
        options: list[tuple[int, str]] = []
        for row in self.db.list_report_contexts():
            report_id = int(row["id"])
            if report_id == self.current_report_id:
                continue
            version_name = row["version_name"] or f"v{row['version_number']}"
            options.append(
                (
                    report_id,
                    (
                        f"{row['project_name']} > "
                        f"{row['business_code']} {row['business_name']} > "
                        f"{row['report_number']} / {row['pipe_number']} > "
                        f"{version_name}"
                    ),
                )
            )
        return options

    @staticmethod
    def _merge_row_value(row, field: str) -> object:
        if row is None:
            return None
        try:
            return row[field]
        except Exception:
            return None

    @staticmethod
    def _normalize_merge_value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.6f}".rstrip("0").rstrip(".")
        return str(value).strip()

    def _report_merge_snapshot(self, report_id: int) -> dict[str, str]:
        context = self.db.get_report_context(report_id)
        report = self.db.get_report(report_id)
        pipe_info = self.db.get_pipe_information(report_id)
        upstream = self.db.get_manhole(report_id, "upstream")
        downstream = self.db.get_manhole(report_id, "downstream")
        actual = self.db.get_actual_survey(report_id)

        snapshot: dict[str, str] = {}
        for field, label in CONTEXT_MERGE_LABELS:
            snapshot[f"프로젝트/사업 > {label}"] = self._normalize_merge_value(
                self._merge_row_value(context, field)
            )
        for field, label in REPORT_FIELD_LABELS:
            if field == "report_number":
                continue
            snapshot[f"보고서 정보 > {label}"] = self._normalize_merge_value(
                self._merge_row_value(report, field)
            )
        for field, label in PIPE_MERGE_LABELS:
            snapshot[f"관로 정보 > {label}"] = self._normalize_merge_value(
                self._merge_row_value(pipe_info, field)
            )
        for role_label, row in (("상류맨홀", upstream), ("하류맨홀", downstream)):
            for field, label in MANHOLE_LABELS:
                snapshot[f"{role_label} > {label}"] = self._normalize_merge_value(
                    self._merge_row_value(row, field)
                )
        for field, label in ACTUAL_SURVEY_LABELS:
            snapshot[f"실제 조사 정보 > {label}"] = self._normalize_merge_value(
                self._merge_row_value(actual, field)
            )
        return snapshot

    def _pdf_after_report_mismatches(
        self, before_report_id: int, after_report_id: int
    ) -> list[str]:
        before_snapshot = self._report_merge_snapshot(before_report_id)
        after_snapshot = self._report_merge_snapshot(after_report_id)
        mismatches: list[str] = []
        for label, before_value in before_snapshot.items():
            after_value = after_snapshot.get(label, "")
            if before_value != after_value:
                mismatches.append(
                    f"{label}: 보수전='{before_value or '-'}', 보수후='{after_value or '-'}'"
                )
        return mismatches

    def _pdf_defect_payload(self, report_id: int) -> tuple[list[dict], float]:
        defects: list[dict] = []
        max_distance_m = 0.0
        for row in self.db.list_defects(report_id):
            distance_m = abs(float(row["distance_m"])) if row["distance_m"] is not None else 0.0
            max_distance_m = max(max_distance_m, distance_m)
            defects.append(
                {
                    "distance_m": distance_m,
                    "drive_direction": row["drive_direction"] or "",
                    "item_category": row["item_category"] or "",
                    "grade": row["grade"] or "",
                    "image_path": row["image_path"],
                    "condition_item": row["condition_item"] or "",
                    "defect_item": row["defect_item"] or "",
                    "timestamp_ms": int(row["timestamp_ms"]),
                }
            )
        return defects, max_distance_m

    def generate_visual_pdf_report(
        self,
        report_type: str = "inspection",
        before_report_id: int | None = None,
        after_report_id: int | None = None,
        output_dir: Path | None = None,
    ) -> None:
        if self.current_report_id is None:
            return
        self.save_report_details_without_message()
        if before_report_id is None:
            before_report_id = self.current_report_id
        is_comparison = report_type == "comparison"
        if is_comparison and after_report_id is None:
            QMessageBox.warning(self, "PDF 보고서", "비교할 보수후 보고서를 선택하세요.")
            return

        if is_comparison and after_report_id is not None:
            mismatches = self._pdf_after_report_mismatches(
                before_report_id, after_report_id
            )
            if mismatches:
                mismatch_preview = "\n".join(f"- {item}" for item in mismatches[:12])
                if len(mismatches) > 12:
                    mismatch_preview += f"\n- 외 {len(mismatches) - 12}개"
                QMessageBox.warning(
                    self,
                    "PDF 보고서",
                    (
                        "보수후 보고서의 기본 정보가 보수전 보고서와 달라 비교 PDF를 생성할 수 없습니다.\n\n"
                        f"{mismatch_preview}"
                    ),
                )
                return
        context = self.db.get_report_context(before_report_id)
        pipe_info = self.db.get_pipe_information(before_report_id)
        if context is None or pipe_info is None:
            return
        upstream = self.db.get_manhole(before_report_id, "upstream")
        downstream = self.db.get_manhole(before_report_id, "downstream")
        actual = self.db.get_actual_survey(before_report_id)
        pipe_png_path = self._resolve_default_pipe_png_path()
        if pipe_png_path is None:
            QMessageBox.warning(self, "PDF 보고서", "pipe.png 템플릿 파일을 찾을 수 없습니다")
            return
        defects_before, before_max_distance_m = self._pdf_defect_payload(
            before_report_id
        )
        if not is_comparison or after_report_id is None:
            defects_after: list[dict] = []
            after_max_distance_m = 0.0
        else:
            defects_after, after_max_distance_m = self._pdf_defect_payload(
                after_report_id
            )
        max_distance_m = max(before_max_distance_m, after_max_distance_m)
        if output_dir is None:
            output_dir = self.inspection.storage.pdf_report_dir(
                context["project_name"],
                context["business_code"],
                context["business_name"],
                context["report_number"],
                context["pipe_number"],
                context["version_name"],
            )
        try:
            pipe_length = float(pipe_info["length_m"] or 0.0)
            if pipe_length <= 0 and max_distance_m > 0:
                pipe_length = max_distance_m
            pdf_path = generate_pipe_pdf_report(
                project_name=context["project_name"],
                zone_name=context["business_name"],
                pipe_code=context["report_number"],
                pipe_length_m=pipe_length,
                pipe_png_path=pipe_png_path,
                defects_before=defects_before,
                defects_after=defects_after,
                output_path=str(output_dir),
                report_type=report_type,
                report_context=context,
                pipe_info=pipe_info,
                upstream_manhole=upstream,
                downstream_manhole=downstream,
                actual_survey=actual,
            )
        except Exception as exc:
            self.logger.exception("PDF report generation failed")
            QMessageBox.critical(self, "PDF 보고서 오류", str(exc))
            return
        QMessageBox.information(self, "PDF 보고서", f"PDF 보고서가 생성되었습니다:\n{pdf_path}")
        self._open_generated_file(pdf_path)


class LeftNavigationPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("leftPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setSpacing(8)
        self._apply_margins()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_margins()

    def _apply_margins(self) -> None:
        width = self.width()
        horizontal = 4 if width < 230 else 6 if width < 300 else 10
        self.content_layout.setContentsMargins(horizontal, 10, horizontal, 10)


class NavSectionTitle(QLabel):
    clicked = Signal()

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(title, parent)
        self._clickable = False

    def set_clickable(self, enabled: bool) -> None:
        self._clickable = enabled
        self.setProperty("clickable", "true" if enabled else "false")
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if enabled
            else Qt.CursorShape.ArrowCursor
        )
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class NavSectionGroup(QGroupBox):
    title_clicked = Signal()

    def __init__(self, title: str, widget: QWidget, parent=None) -> None:
        super().__init__("", parent)
        self.setObjectName("navSection")
        self.header_label = NavSectionTitle(title, self)
        self.header_label.setObjectName("navSectionTitle")
        level = {"프로젝트": "project", "사업": "business", "보고서": "report"}.get(
            title, "default"
        )
        self.header_label.setProperty("navLevel", level)
        self.header_label.clicked.connect(self.title_clicked.emit)
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setSpacing(0)
        self.content_layout.addWidget(self.header_label)
        self.content_layout.addWidget(widget)
        self._apply_margins()

    def set_title_clickable(self, enabled: bool) -> None:
        self.header_label.set_clickable(enabled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_margins()

    def _apply_margins(self) -> None:
        self.content_layout.setContentsMargins(0, 0, 0, 4)


class ReportGroupNavWidget(QWidget):
    def __init__(
        self,
        label: str,
        *,
        expanded: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(4)
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setStyleSheet("color: #d9e3f1; background: transparent;")
        self.label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.toggle_button = QToolButton(self)
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.setFixedSize(24, 24)
        self.toggle_button.setStyleSheet(
            "QToolButton { border: 0; color: #d9e3f1; background: transparent; }"
            "QToolButton:hover { background: rgba(255, 255, 255, 0.08); }"
        )
        layout.addWidget(self.label, 1)
        layout.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignRight)
        self.set_label(label)
        self.set_expanded(expanded)

    def set_label(self, label: str) -> None:
        self.label.setText(f"  • {label}")

    def set_expanded(self, expanded: bool) -> None:
        arrow = Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        self.toggle_button.setArrowType(arrow)

    def mousePressEvent(self, event) -> None:
        widget = self.parent()
        while widget is not None and not isinstance(widget, QListWidget):
            widget = widget.parent()
        if isinstance(widget, QListWidget):
            for row in range(widget.count()):
                item = widget.item(row)
                if widget.itemWidget(item) is self:
                    widget.setCurrentItem(item)
                    item.setSelected(True)
                    break
        super().mousePressEvent(event)


class NavigationList(QListWidget):
    ITEM_HEIGHT = 38

    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("navList")
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setSpacing(0)
        self.setAlternatingRowColors(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def format_label(self, label: str) -> str:
        return f"  • {label}"

    def create_item(
        self,
        entity_id: int,
        label: str,
        *,
        nav_type: str | None = None,
        version_group_id: int | None = None,
    ) -> QListWidgetItem:
        nav_type = nav_type or self.kind
        display_label = self.format_label(label)
        if nav_type == "report_version":
            display_label = f"     {label}"
        item = QListWidgetItem(display_label)
        item.setData(ROLE_KIND, self.kind)
        item.setData(ROLE_ID, entity_id)
        item.setData(ROLE_NAV_TYPE, nav_type)
        if version_group_id is not None:
            item.setData(ROLE_VERSION_GROUP_ID, version_group_id)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        item.setSizeHint(QSize(self._item_width(), self.ITEM_HEIGHT))
        item.setForeground(QColor("#b8c6d9" if nav_type == "report_version" else "#d9e3f1"))
        font = item.font()
        font.setBold(self.kind in {"project", "business"})
        if nav_type == "report_version":
            point_size = font.pointSize()
            if point_size <= 0:
                point_size = QApplication.font().pointSize()
            font.setPointSize(max(9, point_size - 1))
        item.setFont(font)
        return item

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_item_widths()

    def _item_width(self) -> int:
        return max(40, self.viewport().width())

    def _apply_item_widths(self) -> None:
        size = QSize(self._item_width(), self.ITEM_HEIGHT)
        for row in range(self.count()):
            self.item(row).setSizeHint(size)
