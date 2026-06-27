from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from sewerpipe_inspector.licensing.errors import LicenseApiError
from sewerpipe_inspector.licensing.license_service import (
    LicenseService,
    LicenseStatus,
    normalize_license_key,
)


ERROR_MESSAGES = {
    "INVALID_LICENSE_KEY": "라이선스 키를 확인할 수 없습니다.",
    "REVOKED_LICENSE_KEY": "폐기된 라이선스 키입니다.",
    "EXPIRED_LICENSE": "라이선스가 만료되었습니다.",
    "SUSPENDED_LICENSE": "라이선스가 일시 중지되었습니다.",
    "DEVICE_LIMIT_EXCEEDED": "활성화 가능한 장치 수를 초과했습니다.",
    "DEVICE_REVOKED": "이 장치는 비활성화되었습니다.",
    "UNSUPPORTED_APP_VERSION": "이 버전은 더 이상 지원되지 않습니다.",
    "RATE_LIMITED": "잠시 후 다시 시도하세요.",
}


class _ActivationWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, service: LicenseService, license_key: str) -> None:
        super().__init__()
        self.service = service
        self.license_key = license_key

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self.service.activate(self.license_key))
        except LicenseApiError as exc:
            self.failed.emit(ERROR_MESSAGES.get(exc.code, exc.message))
        except Exception:
            self.failed.emit("라이선스 활성화 중 오류가 발생했습니다.")
        finally:
            self.finished.emit()


class LicenseActivationDialog(QDialog):
    def __init__(self, service: LicenseService) -> None:
        super().__init__()
        self.service = service
        self.license_status: LicenseStatus | None = None
        self._thread: QThread | None = None
        self._worker: _ActivationWorker | None = None
        self.setWindowTitle("PIPE1 라이선스 활성화")
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("라이선스 활성화")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("PIPE1-XXXX-XXXX-XXXX-XXXX")
        self.key_input.returnPressed.connect(self._activate)
        layout.addWidget(self.key_input)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("취소")
        self.cancel_button.clicked.connect(self.reject)
        self.activate_button = QPushButton("활성화")
        self.activate_button.clicked.connect(self._activate)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.activate_button)
        layout.addLayout(buttons)

    def _activate(self) -> None:
        license_key = normalize_license_key(self.key_input.text())
        if not license_key:
            self.status_label.setText("라이선스 키를 입력하세요.")
            return
        self.key_input.setText(license_key)
        self.activate_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status_label.setText("활성화 중입니다.")

        self._thread = QThread(self)
        self._worker = _ActivationWorker(self.service, license_key)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_success(self, status: LicenseStatus) -> None:
        self.license_status = status
        self.accept()

    def _on_failure(self, message: str) -> None:
        self.status_label.setText(message)
        self.activate_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
