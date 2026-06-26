from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QFileDialog, QMessageBox

from sewerpipe_inspector.db import Database
from sewerpipe_inspector.fonts import APP_FONT_FAMILY, find_app_font_paths
from sewerpipe_inspector.licensing.config import (
    LicenseRuntimeConfig,
    build_license_service,
    load_license_runtime_config,
)
from sewerpipe_inspector.licensing.errors import LicenseConfigurationError
from sewerpipe_inspector.licensing.license_service import LicenseService, LicenseStatus
from sewerpipe_inspector.logging_config import configure_logging
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.report_service import ReportService
from sewerpipe_inspector.services.storage_service import StorageService
from sewerpipe_inspector.services.training_upload_service import (
    TrainingUploadClient,
    TrainingUploadService,
)
from sewerpipe_inspector.settings_service import load_settings, save_settings
from sewerpipe_inspector.ui.license_dialog import LicenseActivationDialog
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


def _ensure_license_activation() -> (
    tuple[LicenseService | None, LicenseStatus | None, LicenseRuntimeConfig] | None
):
    try:
        config = load_license_runtime_config()
    except LicenseConfigurationError as exc:
        QMessageBox.critical(None, "라이선스 설정 오류", str(exc))
        return None

    if not config.is_configured:
        if config.require_activation:
            QMessageBox.critical(
                None,
                "라이선스 설정 오류",
                "라이선스 서버 주소와 공개키 설정이 필요합니다.",
            )
            return None
        return None, None, config

    try:
        service = build_license_service(config)
    except LicenseConfigurationError as exc:
        QMessageBox.critical(None, "라이선스 설정 오류", str(exc))
        return None

    status = service.current_status()
    if status.status == "active":
        return service, status, config

    dialog = LicenseActivationDialog(service)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.license_status is None:
        return None
    return service, dialog.license_status, config


def _build_training_upload_service(
    db: Database,
    license_status: LicenseStatus | None,
    config: LicenseRuntimeConfig,
) -> TrainingUploadService | None:
    if license_status is None or not license_status.can_use_feature("training_upload"):
        return None
    if not config.api_base_url or not license_status.license_id:
        return None
    device_id = license_status.payload.get("device_id")
    if not isinstance(device_id, str) or not device_id:
        return None

    consent_type = "capture_images_and_labels"
    consent = db.get_training_upload_consent(
        license_status.license_id, device_id, consent_type
    )
    if consent is None:
        accepted = _ask_training_upload_consent()
        db.set_training_upload_consent(
            license_id=license_status.license_id,
            device_id=device_id,
            consent_type=consent_type,
            consent_version="2026-06-25",
            accepted=accepted,
            app_version=config.app_version,
        )
        consent = db.get_training_upload_consent(
            license_status.license_id, device_id, consent_type
        )
    consent_enabled = bool(consent is not None and consent["accepted"])
    consent_version = (
        str(consent["consent_version"]) if consent is not None else "2026-06-25"
    )
    return TrainingUploadService(
        db,
        client=TrainingUploadClient(
            config.api_base_url,
            upload_token=license_status.device_upload_token,
        ),
        license_id=license_status.license_id,
        device_id=device_id,
        consent_enabled=consent_enabled,
        consent_type=consent_type,
        consent_version=consent_version,
        app_version=config.app_version,
    )


def _ask_training_upload_consent() -> bool:
    result = QMessageBox.question(
        None,
        "학습 데이터 업로드 동의",
        (
            "캡처 이미지와 결함/상태 라벨을 Pipe1 학습용 서버로 전송할까요?\n\n"
            "원본 영상, 생성된 Excel/PDF, 메모, 주소 정보는 전송하지 않습니다."
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return result == QMessageBox.StandardButton.Yes


def run() -> None:
    app = QApplication(sys.argv)
    _apply_application_font(app)
    license_context = _ensure_license_activation()
    if license_context is None:
        return
    _, license_status, license_config = license_context
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
        training_upload_service = _build_training_upload_service(
            db, license_status, license_config
        )
        inspection = InspectionService(
            db, storage, report, training_upload_service=training_upload_service
        )
        window = MainWindow(db, inspection)
        if license_status is not None and license_status.masked_license_key:
            window.statusBar().showMessage(
                f"라이선스 활성화됨: {license_status.masked_license_key}"
            )
        window.show()
        app.exec()
    except Exception:
        logging.exception("Fatal startup failure")
        QMessageBox.critical(
            None,
            "치명적 오류",
            "애플리케이션 시작에 실패했습니다. error.log를 확인하세요.",
        )
