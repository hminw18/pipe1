from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, QThread, Signal, Slot
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QFileDialog, QMessageBox

from sewerpipe_inspector.db import Database
from sewerpipe_inspector.fonts import APP_FONT_FAMILY, find_app_font_paths
from sewerpipe_inspector.licensing.config import (
    LicenseRuntimeConfig,
    build_update_service,
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
from sewerpipe_inspector.updates.models import UpdateInfo
from sewerpipe_inspector.updates.update_service import UpdateService

DEFAULT_WORKSPACE_DIRNAME = "pipe1"
_ONLINE_REVALIDATION_REASONS = {
    "license entitlement is expired",
    "offline grace period has expired",
}


class _LicenseValidationWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, service: LicenseService) -> None:
        super().__init__()
        self.service = service

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self.service.current_status(validate_online=True))
        except Exception as exc:
            self.failed.emit(str(exc) or "라이선스 검증 중 오류가 발생했습니다.")
        finally:
            self.finished.emit()


class _LicenseValidationHandler(QObject):
    def __init__(
        self,
        service: LicenseService,
        window: MainWindow,
        *,
        require_activation: bool,
    ) -> None:
        super().__init__(window)
        self.service = service
        self.window = window
        self.require_activation = require_activation

    @Slot(object)
    def on_succeeded(self, status: LicenseStatus) -> None:
        _handle_background_license_status(
            self.service,
            self.window,
            status,
            require_activation=self.require_activation,
        )

    @Slot(str)
    def on_failed(self, message: str) -> None:
        _show_background_license_message(
            self.window,
            f"라이선스 온라인 검증 실패 - 오프라인 유예 사용 중: {message}",
        )


class _UpdateCheckWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, service: UpdateService) -> None:
        super().__init__()
        self.service = service

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self.service.check_for_update())
        except Exception as exc:
            self.failed.emit(str(exc) or "업데이트 확인 중 오류가 발생했습니다.")
        finally:
            self.finished.emit()


class _UpdateCheckHandler(QObject):
    def __init__(self, service: UpdateService, window: MainWindow) -> None:
        super().__init__(window)
        self.service = service
        self.window = window

    @Slot(object)
    def on_succeeded(self, info: UpdateInfo) -> None:
        self.window.set_update_info(info)
        if not info.update_available:
            return
        if info.mandatory:
            self.window.prompt_update(info, mandatory=True)
            return
        latest = info.latest_version or "새 버전"
        self.window.statusBar().showMessage(
            f"PIPE1 {latest} 업데이트를 사용할 수 있습니다. 설정에서 설치할 수 있습니다.",
            15000,
        )

    @Slot(str)
    def on_failed(self, message: str) -> None:
        logging.getLogger(__name__).warning("업데이트 확인 실패: %s", message)


def _default_workspace_root() -> Path:
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    documents_path = Path(documents) if documents else Path.home() / "Documents"
    return documents_path / DEFAULT_WORKSPACE_DIRNAME


def _ensure_workspace_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        QMessageBox.critical(
            None,
            "작업 폴더 오류",
            f"작업 폴더를 만들 수 없습니다.\n\n{path}\n\n{exc}",
        )
        return False
    if not path.exists() or not path.is_dir():
        QMessageBox.critical(None, "작업 폴더 오류", f"작업 폴더가 아닙니다.\n\n{path}")
        return False
    return True


def _ask_use_default_workspace(path: Path) -> bool:
    result = QMessageBox.question(
        None,
        "기본 작업 폴더",
        f"기본 폴더로 작업폴더를 설정하시겠습니까?\n\n{path}",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    return result == QMessageBox.StandardButton.Yes


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
    if _requires_online_revalidation(status):
        status = service.current_status(validate_online=True)
        if status.status == "active":
            return service, status, config
    if not config.require_activation:
        return service, None, config

    dialog = LicenseActivationDialog(service)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.license_status is None:
        return None
    return service, dialog.license_status, config


def _requires_online_revalidation(status: LicenseStatus) -> bool:
    return status.status == "invalid" and status.reason in _ONLINE_REVALIDATION_REASONS


def _start_background_license_validation(
    service: LicenseService,
    window: MainWindow,
    *,
    require_activation: bool,
) -> None:
    thread = QThread(window)
    worker = _LicenseValidationWorker(service)
    handler = _LicenseValidationHandler(
        service,
        window,
        require_activation=require_activation,
    )
    worker.moveToThread(thread)
    active_threads = getattr(window, "_license_validation_threads", [])
    active_threads.append((thread, worker, handler))
    setattr(window, "_license_validation_threads", active_threads)

    def cleanup() -> None:
        active = getattr(window, "_license_validation_threads", [])
        try:
            active.remove((thread, worker, handler))
        except ValueError:
            pass
        setattr(window, "_license_validation_threads", active)

    thread.started.connect(worker.run)
    worker.succeeded.connect(handler.on_succeeded)
    worker.failed.connect(handler.on_failed)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(cleanup)
    thread.finished.connect(thread.deleteLater)
    thread.start()


def _start_background_update_check(
    service: UpdateService,
    window: MainWindow,
) -> None:
    thread = QThread(window)
    worker = _UpdateCheckWorker(service)
    handler = _UpdateCheckHandler(service, window)
    worker.moveToThread(thread)
    active_threads = getattr(window, "_update_check_threads", [])
    active_threads.append((thread, worker, handler))
    setattr(window, "_update_check_threads", active_threads)

    def cleanup() -> None:
        active = getattr(window, "_update_check_threads", [])
        try:
            active.remove((thread, worker, handler))
        except ValueError:
            pass
        setattr(window, "_update_check_threads", active)

    thread.started.connect(worker.run)
    worker.succeeded.connect(handler.on_succeeded)
    worker.failed.connect(handler.on_failed)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(cleanup)
    thread.finished.connect(thread.deleteLater)
    thread.start()


def _handle_background_license_status(
    service: LicenseService,
    window: MainWindow,
    status: LicenseStatus,
    *,
    require_activation: bool,
) -> None:
    window.license_status = status
    if status.status == "active":
        if status.reason:
            _show_background_license_message(
                window,
                f"라이선스 온라인 검증 실패 - 오프라인 유예 사용 중: {status.reason}",
            )
        elif status.masked_license_key:
            window.statusBar().showMessage(
                f"라이선스 온라인 검증 완료: {status.masked_license_key}", 5000
            )
        upload_service = getattr(window.inspection, "training_upload_service", None)
        if upload_service is not None and status.device_upload_token:
            upload_service.client.upload_token = status.device_upload_token
        return

    message = f"라이선스 검증 실패: {status.reason or status.status}"
    _show_background_license_message(window, message)
    if require_activation:
        _prompt_for_license_reactivation(service, window, message)


def _prompt_for_license_reactivation(
    service: LicenseService,
    window: MainWindow,
    message: str,
) -> None:
    dialog = LicenseActivationDialog(service)
    if hasattr(dialog, "status_label"):
        dialog.status_label.setText(f"{message}\n새 라이선스 키를 입력하세요.")
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.license_status is None:
        _show_background_license_message(
            window,
            "라이선스가 갱신되지 않았습니다. 새 라이선스 키가 필요합니다.",
        )
        _exit_after_license_failure(window)
        return

    window.license_status = dialog.license_status
    # A replacement key can belong to a different license. Drop the old upload
    # service so queued server uploads cannot continue under stale license IDs.
    if hasattr(window.inspection, "training_upload_service"):
        window.inspection.training_upload_service = None
    if dialog.license_status.masked_license_key:
        window.statusBar().showMessage(
            f"라이선스 갱신 완료: {dialog.license_status.masked_license_key}",
            5000,
        )


def _exit_after_license_failure(window: MainWindow) -> None:
    window.setEnabled(False)
    window.close()
    app = QApplication.instance()
    if app is not None:
        app.quit()


def _show_background_license_message(window: MainWindow, message: str) -> None:
    logging.getLogger(__name__).warning(message)
    window.statusBar().showMessage(message, 15000)


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
    license_service, license_status, license_config = license_context
    settings = load_settings()

    workspace: Path | None = None
    workspace_from_saved_default = False
    if settings.default_workspace and not settings.always_show_directory_picker:
        candidate = Path(settings.default_workspace).expanduser()
        if _ensure_workspace_dir(candidate):
            workspace = candidate
            workspace_from_saved_default = True

    if workspace is None:
        default_root = _default_workspace_root()
        if not settings.default_workspace and _ask_use_default_workspace(default_root):
            if not _ensure_workspace_dir(default_root):
                return
            workspace = default_root
            settings.default_workspace = str(workspace)
            settings.always_show_directory_picker = False
            settings.suppress_default_workspace_prompt = True
            save_settings(settings)

    if workspace is None:
        default_root = _default_workspace_root()
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

    if (
        workspace is not None
        and not workspace_from_saved_default
        and not settings.default_workspace
    ):
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
        if result == QMessageBox.StandardButton.Yes:
            settings.default_workspace = str(workspace)
            settings.always_show_directory_picker = False
            settings.suppress_default_workspace_prompt = True
        elif never_ask_checkbox.isChecked():
            settings.always_show_directory_picker = True
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
        update_service = build_update_service(license_config)
        inspection = InspectionService(
            db, storage, report, training_upload_service=training_upload_service
        )
        window = MainWindow(
            db,
            inspection,
            license_service=license_service,
            license_status=license_status,
            license_config=license_config,
            update_service=update_service,
        )
        if license_status is not None and license_status.masked_license_key:
            window.statusBar().showMessage(
                f"라이선스 활성화됨: {license_status.masked_license_key}"
            )
        window.show()
        if license_service is not None and license_status is not None:
            _start_background_license_validation(
                license_service,
                window,
                require_activation=license_config.require_activation,
            )
        if update_service is not None:
            _start_background_update_check(update_service, window)
        app.exec()
    except Exception:
        logging.exception("Fatal startup failure")
        QMessageBox.critical(
            None,
            "치명적 오류",
            "애플리케이션 시작에 실패했습니다. error.log를 확인하세요.",
        )
