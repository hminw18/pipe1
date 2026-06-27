from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sewerpipe_inspector.settings_service import load_settings, save_settings

if TYPE_CHECKING:
    from sewerpipe_inspector.db import Database
    from sewerpipe_inspector.licensing.config import LicenseRuntimeConfig
    from sewerpipe_inspector.licensing.license_service import LicenseStatus
    from sewerpipe_inspector.services.training_upload_service import TrainingUploadService


TRAINING_CONSENT_TYPE = "capture_images_and_labels"
TRAINING_CONSENT_VERSION = "2026-06-25"


class SettingsDialog(QDialog):
    def __init__(
        self,
        *,
        db: Database,
        current_workspace: Path,
        license_status: LicenseStatus | None,
        license_config: LicenseRuntimeConfig | None,
        training_upload_service: TrainingUploadService | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.current_workspace = current_workspace
        self.license_status = license_status
        self.license_config = license_config
        self.training_upload_service = training_upload_service
        self.settings = load_settings()
        self.training_upload_consent_changed = False

        self.setWindowTitle("설정")
        self.resize(680, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        tabs = QTabWidget(self)
        tabs.setDocumentMode(False)
        tabs.tabBar().setExpanding(False)
        tabs.tabBar().setUsesScrollButtons(False)
        tabs.setStyleSheet(
            """
            QTabWidget::pane {
                background: #ffffff;
                border: 0;
                margin: 0;
            }
            QTabWidget, QTabBar {
                background: #ffffff;
            }
            QTabWidget::tab-bar {
                left: 0;
            }
            QWidget#reportExportPanel {
                background: #ffffff;
                border: 1px solid #d7dde8;
            }
            QWidget#reportExportPanel QLabel {
                background: transparent;
            }
            QTabBar::tab {
                background: #ffffff;
                border: 1px solid #d7dde8;
                color: #24324a;
                padding: 8px 14px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #111827;
                border-bottom: 1px solid #ffffff;
            }
            """
        )
        tabs.addTab(self._build_general_tab(), "일반")
        tabs.addTab(self._build_license_tab(), "라이선스")
        tabs.addTab(self._build_training_upload_tab(), "학습 데이터")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_general_tab(self) -> QWidget:
        tab = QWidget(self)
        tab.setObjectName("reportExportPanel")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        grid = _settings_grid()
        row = 0
        row = _add_settings_row(
            grid, row, "현재 작업 폴더", QLabel(str(self.current_workspace), tab)
        )

        default_row = QWidget(tab)
        default_layout = QHBoxLayout(default_row)
        default_layout.setContentsMargins(0, 0, 0, 0)
        default_layout.setSpacing(6)
        self.default_workspace_input = QLineEdit(
            self.settings.default_workspace or "", default_row
        )
        self.default_workspace_input.setMinimumWidth(360)
        browse_button = QPushButton("찾기", default_row)
        browse_button.clicked.connect(self._browse_default_workspace)
        clear_button = QPushButton("비우기", default_row)
        clear_button.clicked.connect(self.default_workspace_input.clear)
        default_layout.addWidget(self.default_workspace_input, 1)
        default_layout.addWidget(browse_button)
        default_layout.addWidget(clear_button)
        row = _add_settings_row(grid, row, "기본 작업 폴더", default_row)

        self.always_pick_workspace_checkbox = QCheckBox(
            "앱 시작 시 작업 폴더 선택 창을 표시", tab
        )
        self.always_pick_workspace_checkbox.setChecked(
            self.settings.always_show_directory_picker
        )
        row = _add_settings_row(grid, row, "", self.always_pick_workspace_checkbox)
        layout.addLayout(grid)

        note = QLabel(
            "기본 작업 폴더를 비워두면 시작 시 작업 폴더를 선택합니다.",
            tab,
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return tab

    def _build_license_tab(self) -> QWidget:
        tab = QWidget(self)
        tab.setObjectName("reportExportPanel")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        grid = _settings_grid()
        row = 0
        status = self.license_status
        config = self.license_config
        row = _add_settings_row(grid, row, "실행 모드", QLabel(self._runtime_mode_text(), tab))
        row = _add_settings_row(grid, row, "라이선스 상태", QLabel(self._license_status_text(), tab))
        row = _add_settings_row(grid, row, "서버 구성", QLabel(self._license_configuration_text(), tab))
        row = _add_settings_row(grid, row, "활성화 필요", QLabel(self._activation_requirement_text(), tab))
        row = _add_settings_row(
            grid,
            row,
            "라이선스 키",
            QLabel(_display_text(status.masked_license_key if status else None), tab),
        )
        row = _add_settings_row(
            grid,
            row,
            "라이선스 ID",
            QLabel(_display_text(status.license_id if status else None), tab),
        )
        row = _add_settings_row(grid, row, "디바이스 ID", QLabel(_display_text(self._device_id()), tab))
        row = _add_settings_row(
            grid,
            row,
            "앱 버전",
            QLabel(_display_text(config.app_version if config else None), tab),
        )
        row = _add_settings_row(
            grid,
            row,
            "라이선스 서버",
            QLabel(_display_text(config.api_base_url if config else None), tab),
        )

        features = self._effective_license_features()
        feature_rows = [
            ("로컬 보고서", bool(features.get("local_report"))),
            ("학습 데이터 업로드", bool(features.get("training_upload"))),
        ]
        for label, enabled in feature_rows:
            row = _add_settings_row(
                grid,
                row,
                label,
                QLabel("사용 가능" if enabled else "사용 불가", tab),
            )
        layout.addLayout(grid)
        layout.addStretch(1)
        return tab

    def _build_training_upload_tab(self) -> QWidget:
        tab = QWidget(self)
        tab.setObjectName("reportExportPanel")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        grid = _settings_grid()
        row = 0
        eligible = self._can_configure_training_upload()
        consent = self._training_upload_consent()
        self.initial_training_upload_consent = bool(
            consent is not None and consent["accepted"]
        )
        self.training_upload_checkbox = QCheckBox(
            "캡처 이미지와 결함/상태 라벨 업로드 허용", tab
        )
        self.training_upload_checkbox.setChecked(self.initial_training_upload_consent)
        self.training_upload_checkbox.setEnabled(eligible)
        row = _add_settings_row(grid, row, "상태", QLabel(self._training_upload_status_text(eligible), tab))
        row = _add_settings_row(grid, row, "동의", self.training_upload_checkbox)
        row = _add_settings_row(grid, row, "동의 버전", QLabel(TRAINING_CONSENT_VERSION, tab))
        row = _add_settings_row(
            grid,
            row,
            "수락 일시",
            QLabel(_display_text(consent["accepted_at"] if consent else None), tab),
        )
        row = _add_settings_row(
            grid,
            row,
            "철회 일시",
            QLabel(_display_text(consent["revoked_at"] if consent else None), tab),
        )

        snapshot_counts, sample_counts = self._training_upload_counts()
        row = _add_settings_row(grid, row, "스냅샷", QLabel(_format_counts(snapshot_counts), tab))
        row = _add_settings_row(grid, row, "샘플", QLabel(_format_counts(sample_counts), tab))
        layout.addLayout(grid)

        note = QLabel(
            "동의를 꺼도 이미 생성된 로컬 보고서와 캡처 파일은 삭제되지 않습니다.",
            tab,
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return tab

    def _browse_default_workspace(self) -> None:
        start_dir = self.default_workspace_input.text().strip() or str(
            self.current_workspace
        )
        selected = QFileDialog.getExistingDirectory(
            self, "기본 작업 폴더 선택", start_dir
        )
        if selected:
            self.default_workspace_input.setText(selected)

    def accept(self) -> None:
        if not self._save_general_settings():
            return
        self._save_training_upload_consent()
        super().accept()

    def _save_general_settings(self) -> bool:
        default_workspace = self.default_workspace_input.text().strip()
        normalized_workspace: str | None = None
        if default_workspace:
            path = Path(default_workspace).expanduser()
            if not path.exists():
                result = QMessageBox.question(
                    self,
                    "기본 작업 폴더",
                    "입력한 폴더가 없습니다. 새로 만들까요?\n\n" + str(path),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if result != QMessageBox.StandardButton.Yes:
                    self.default_workspace_input.setFocus()
                    return False
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    QMessageBox.warning(
                        self,
                        "기본 작업 폴더",
                        f"폴더를 만들 수 없습니다.\n\n{path}\n\n{exc}",
                    )
                    self.default_workspace_input.setFocus()
                    return False
            if not path.is_dir():
                QMessageBox.warning(self, "기본 작업 폴더", "폴더 경로를 입력하세요.")
                self.default_workspace_input.setFocus()
                return False
            normalized_workspace = str(path)

        self.settings.default_workspace = normalized_workspace
        self.settings.always_show_directory_picker = (
            self.always_pick_workspace_checkbox.isChecked()
        )
        self.settings.suppress_default_workspace_prompt = bool(
            normalized_workspace or self.settings.always_show_directory_picker
        )
        save_settings(self.settings)
        return True

    def _save_training_upload_consent(self) -> None:
        if not self._can_configure_training_upload():
            return
        license_id = self._license_id()
        device_id = self._device_id()
        if not license_id or not device_id:
            return

        accepted = self.training_upload_checkbox.isChecked()
        if accepted == self.initial_training_upload_consent:
            return

        app_version = (
            self.license_config.app_version
            if self.license_config is not None
            else "0.1.0"
        )
        self.db.set_training_upload_consent(
            license_id=license_id,
            device_id=device_id,
            consent_type=TRAINING_CONSENT_TYPE,
            consent_version=TRAINING_CONSENT_VERSION,
            accepted=accepted,
            app_version=app_version,
        )
        if self.training_upload_service is not None:
            self.training_upload_service.consent_enabled = accepted
            self.training_upload_service.consent_version = TRAINING_CONSENT_VERSION
        self.training_upload_consent_changed = True

    def _license_status_text(self) -> str:
        if self.license_status is None:
            if (
                self.license_config is not None
                and not self.license_config.require_activation
            ):
                return "개발 모드 - 라이선스 활성화 생략"
            if (
                self.license_config is not None
                and not self.license_config.is_configured
            ):
                return "라이선스 서버 미설정"
            return "비활성"
        if self.license_status.status == "active":
            return "활성"
        if self.license_status.reason:
            return f"{self.license_status.status} ({self.license_status.reason})"
        return self.license_status.status

    def _runtime_mode_text(self) -> str:
        if self.license_config is None:
            return "알 수 없음"
        if self.license_config.app_env in {"prod", "production"}:
            return "production"
        return f"{self.license_config.app_env} (개발 모드)"

    def _license_configuration_text(self) -> str:
        if self.license_config is None:
            return "설정 없음"
        if self.license_config.is_configured:
            return "서버 URL 및 공개키 설정됨"
        return "서버 URL 또는 공개키 미설정"

    def _activation_requirement_text(self) -> str:
        if self.license_config is None:
            return "알 수 없음"
        return "필요" if self.license_config.require_activation else "필요 없음"

    def _effective_license_features(self) -> dict[str, bool]:
        if self.license_status is not None:
            return self.license_status.features
        if (
            self.license_config is not None
            and not self.license_config.require_activation
        ):
            return {"local_report": True, "training_upload": False}
        return {}

    def _license_id(self) -> str | None:
        if self.license_status is None:
            return None
        return self.license_status.license_id

    def _device_id(self) -> str | None:
        if self.license_status is None:
            return None
        device_id = self.license_status.payload.get("device_id")
        return device_id if isinstance(device_id, str) and device_id else None

    def _can_configure_training_upload(self) -> bool:
        return bool(
            self.license_status is not None
            and self.license_status.status == "active"
            and self.license_status.can_use_feature("training_upload")
            and self._license_id()
            and self._device_id()
        )

    def _training_upload_consent(self):
        license_id = self._license_id()
        device_id = self._device_id()
        if not license_id or not device_id:
            return None
        return self.db.get_training_upload_consent(
            license_id, device_id, TRAINING_CONSENT_TYPE
        )

    def _training_upload_status_text(self, eligible: bool) -> str:
        if eligible:
            return "사용 가능"
        if self.license_status is None:
            return "라이선스 정보 없음"
        if not self.license_status.can_use_feature("training_upload"):
            return "라이선스 기능 권한 없음"
        return "디바이스 정보 없음"

    def _training_upload_counts(self) -> tuple[dict[str, int], dict[str, int]]:
        snapshot_counts = {
            "pending": 0,
            "uploading": 0,
            "uploaded": 0,
            "failed": 0,
        }
        sample_counts = {
            "pending": 0,
            "uploaded": 0,
            "failed": 0,
        }
        for snapshot in self.db.list_training_upload_snapshots():
            status = str(snapshot["status"])
            if status in snapshot_counts:
                snapshot_counts[status] += 1
            for sample in self.db.list_training_upload_samples(int(snapshot["id"])):
                sample_status = str(sample["status"])
                if sample_status in sample_counts:
                    sample_counts[sample_status] += 1
        return snapshot_counts, sample_counts


def _display_text(value: object | None) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text or "-"


def _settings_grid() -> QGridLayout:
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(10)
    grid.setColumnMinimumWidth(0, 128)
    grid.setColumnStretch(1, 1)
    return grid


def _add_settings_row(
    grid: QGridLayout, row: int, label_text: str, value_widget: QWidget
) -> int:
    label = QLabel(label_text)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    label.setMinimumWidth(0)
    if isinstance(value_widget, QLabel):
        value_widget.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        value_widget.setWordWrap(True)
        value_widget.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
    grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignLeft)
    grid.addWidget(value_widget, row, 1)
    return row + 1


def _format_counts(counts: dict[str, int]) -> str:
    labels = {
        "pending": "대기",
        "uploading": "업로드중",
        "uploaded": "완료",
        "failed": "실패",
    }
    return " / ".join(
        f"{labels.get(status, status)} {count}" for status, count in counts.items()
    )
