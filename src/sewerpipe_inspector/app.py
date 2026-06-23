from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QMessageBox

from sewerpipe_inspector.db import Database
from sewerpipe_inspector.fonts import APP_FONT_FAMILY, find_app_font_paths
from sewerpipe_inspector.logging_config import configure_logging
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.report_service import ReportService
from sewerpipe_inspector.services.storage_service import StorageService
from sewerpipe_inspector.settings_service import load_settings, save_settings
from sewerpipe_inspector.ui.main_window import MainWindow


def _apply_application_font(app: QApplication) -> None:
    registered_families: list[str] = []

    for font_path in find_app_font_paths():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            continue
        registered_families.extend(QFontDatabase.applicationFontFamilies(font_id))

    if registered_families:
        family = (
            APP_FONT_FAMILY
            if APP_FONT_FAMILY in registered_families
            else registered_families[0]
        )
        font = QFont(family, 10)
        font.setWeight(QFont.Weight.Normal)
        app.setFont(font)


def run() -> None:
    app = QApplication(sys.argv)
    _apply_application_font(app)
    settings = load_settings()

    workspace: Path | None = None
    if (not settings.always_show_directory_picker) and settings.default_workspace:
        candidate = Path(settings.default_workspace)
        if candidate.exists() and candidate.is_dir():
            workspace = candidate

    if workspace is None:
        default_root = Path.home() / "SewerPipeInspectorWorkspace"
        start_dir = (
            settings.default_workspace
            if settings.default_workspace
            else str(default_root)
        )
        root_dir = QFileDialog.getExistingDirectory(
            None,
            "작업 루트 폴더 선택",
            start_dir,
        )
        if not root_dir:
            QMessageBox.information(None, "종료", "작업 폴더가 선택되지 않았습니다.")
            return
        workspace = Path(root_dir)

    if workspace is not None and not settings.suppress_default_workspace_prompt:
        ask_default = QMessageBox()
        ask_default.setIcon(QMessageBox.Icon.Question)
        ask_default.setWindowTitle("기본 폴더 설정")
        ask_default.setText("이 폴더를 기본 작업 폴더로 사용하시겠습니까?")
        ask_default.setInformativeText(str(workspace))
        ask_default.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        ask_default.setDefaultButton(QMessageBox.StandardButton.Yes)
        never_ask_checkbox = QCheckBox("다시는 묻지 않기")
        ask_default.setCheckBox(never_ask_checkbox)

        result = ask_default.exec()
        if never_ask_checkbox.isChecked():
            settings.always_show_directory_picker = True
            settings.suppress_default_workspace_prompt = True
        elif result == QMessageBox.StandardButton.Yes:
            settings.default_workspace = str(workspace)
            settings.always_show_directory_picker = False
            settings.suppress_default_workspace_prompt = True
        save_settings(settings)

    configure_logging(workspace / "error.log")

    try:
        db = Database(workspace / "sewerpipe_inspector.db")
        storage = StorageService(workspace)
        report = ReportService()
        inspection = InspectionService(db, storage, report)
        window = MainWindow(db, inspection)
        window.show()
        app.exec()
    except Exception:
        logging.exception("Fatal startup failure")
        QMessageBox.critical(
            None,
            "치명적 오류",
            "애플리케이션 시작에 실패했습니다. error.log를 확인하세요.",
        )
