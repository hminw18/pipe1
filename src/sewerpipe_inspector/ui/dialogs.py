from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


@dataclass
class BusinessInput:
    business_code: str
    business_name: str
    client: str
    business_start_date: str
    business_end_date: str


@dataclass
class ReportInput:
    report_number: str
    pipe_number: str


class ProjectDialog(QDialog):
    def __init__(self, project_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("프로젝트")
        form = QFormLayout(self)
        self.project_name_input = QLineEdit(project_name)
        form.addRow("프로젝트명", self.project_name_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self) -> str:
        return self.project_name_input.text().strip()

    def accept(self) -> None:
        if not _require_dialog_fields(
            self, [("프로젝트명", self.project_name_input)]
        ):
            return
        super().accept()


class BusinessDialog(QDialog):
    def __init__(
        self,
        business_code: str = "",
        business_name: str = "",
        client: str = "",
        business_start_date: str = "",
        business_end_date: str = "",
        require_all_fields: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("사업")
        self.require_all_fields = require_all_fields
        form = QFormLayout(self)
        self.business_code_input = QLineEdit(business_code)
        self.business_name_input = QLineEdit(business_name)
        self.client_input = QLineEdit(client)
        self.business_start_date_input = QLineEdit(business_start_date)
        self.business_end_date_input = QLineEdit(business_end_date)
        form.addRow("사업코드", self.business_code_input)
        form.addRow("사업명", self.business_name_input)
        form.addRow("발주처", self.client_input)
        form.addRow("사업시작일", self.business_start_date_input)
        form.addRow("사업완료일", self.business_end_date_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> BusinessInput:
        return BusinessInput(
            business_code=self.business_code_input.text().strip(),
            business_name=self.business_name_input.text().strip(),
            client=self.client_input.text().strip(),
            business_start_date=self.business_start_date_input.text().strip(),
            business_end_date=self.business_end_date_input.text().strip(),
        )

    def accept(self) -> None:
        fields = [
            ("사업코드", self.business_code_input),
            ("사업명", self.business_name_input),
        ]
        if self.require_all_fields:
            fields.extend(
                [
                    ("발주처", self.client_input),
                    ("사업시작일", self.business_start_date_input),
                    ("사업완료일", self.business_end_date_input),
                ]
            )
        if not _require_dialog_fields(self, fields):
            return
        super().accept()


class ReportDialog(QDialog):
    def __init__(
        self,
        report_number: str = "",
        pipe_number: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("보고서")
        form = QFormLayout(self)
        self.report_number_input = QLineEdit(report_number)
        self.pipe_number_input = QLineEdit(pipe_number)
        form.addRow("보고서번호", self.report_number_input)
        form.addRow("관로번호", self.pipe_number_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> ReportInput:
        return ReportInput(
            report_number=self.report_number_input.text().strip(),
            pipe_number=self.pipe_number_input.text().strip(),
        )

    def accept(self) -> None:
        if not _require_dialog_fields(
            self,
            [
                ("보고서번호", self.report_number_input),
                ("관로번호", self.pipe_number_input),
            ],
        ):
            return
        super().accept()


def _require_dialog_fields(
    dialog: QDialog, fields: list[tuple[str, QLineEdit]]
) -> bool:
    for label, widget in fields:
        if not widget.text().strip():
            QMessageBox.warning(dialog, "필수 입력", f"{label}을(를) 입력하세요")
            widget.setFocus()
            return False
    return True


class AfterReportDialog(QDialog):
    def __init__(
        self,
        options: list[tuple[int | None, str]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("After 보고서 선택")
        form = QFormLayout(self)
        self.report_combo = QComboBox(self)
        for report_id, label in options:
            self.report_combo.addItem(label, -1 if report_id is None else report_id)
        self.report_combo.setMinimumWidth(520)
        form.addRow("After 보고서", self.report_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def selected_report_id(self) -> int | None:
        value = self.report_combo.currentData()
        if value in (None, -1):
            return None
        return int(value)


class ReportExportDialog(QDialog):
    def __init__(
        self,
        report_options: list[tuple[int, str]],
        current_report_id: int,
        default_output_dir: str,
        excel_filename_examples: dict[str, str],
        excel_report_options: list[tuple[int, str]] | None = None,
        preselected_excel_report_ids: list[int] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("보고서 출력")
        self.resize(820, 430)
        self.excel_filename_examples = excel_filename_examples
        self._syncing_output_dir = False
        self._syncing_excel_report_checks = False
        self.output_dir_inputs: list[QLineEdit] = []

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.setStyleSheet(
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
        layout.addWidget(self.tabs)

        excel_tab = QWidget(self)
        excel_tab.setObjectName("reportExportPanel")
        excel_layout = QVBoxLayout(excel_tab)
        excel_layout.setContentsMargins(12, 12, 12, 12)
        self.excel_type_combo = QComboBox(excel_tab)
        self.excel_type_combo.addItem("내부결함 판독표", "internal_defect")
        self.excel_type_combo.addItem("이상항목 집계표", "defect_aggregate")
        self._configure_export_combo(self.excel_type_combo, minimum_width=220)
        self.include_internal_photos_checkbox = QCheckBox(
            "각 보고서 시트에 사진 포함", excel_tab
        )
        excel_layout.addWidget(
            self._build_export_form_row(
                excel_tab, "보고서 종류", self.excel_type_combo
            )
        )
        self.include_internal_photos_row = self._build_export_control_row(
            excel_tab, self.include_internal_photos_checkbox
        )
        excel_layout.addWidget(self.include_internal_photos_row)
        excel_layout.addWidget(
            QLabel(
                "선택한 보고서로 사업 단위 엑셀 보고서를 출력합니다.",
                excel_tab,
            )
        )
        self.excel_filename_label = QLabel("", excel_tab)
        excel_layout.addWidget(self.excel_filename_label)
        self.excel_select_all_checkbox = QCheckBox("현재 사업 보고서 전체 선택", excel_tab)
        self.excel_select_all_checkbox.setTristate(False)
        self.excel_report_list = QListWidget(excel_tab)
        self.excel_report_list.setMinimumHeight(92)
        self.excel_report_list.setMaximumHeight(120)
        explicit_preselection = preselected_excel_report_ids is not None
        preselected_ids = set(preselected_excel_report_ids or [])
        for report_id, label in excel_report_options or report_options:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, report_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if explicit_preselection:
                checked = int(report_id) in preselected_ids
            else:
                checked = int(report_id) == current_report_id
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.excel_report_list.addItem(item)
        if not explicit_preselection and self.excel_report_list.count() > 1:
            self.excel_select_all_checkbox.setCheckState(Qt.CheckState.Checked)
            for row in range(self.excel_report_list.count()):
                self.excel_report_list.item(row).setCheckState(Qt.CheckState.Checked)
        else:
            self._set_excel_select_all_state_from_items()
        excel_layout.addWidget(self.excel_select_all_checkbox)
        excel_layout.addWidget(self.excel_report_list)
        excel_layout.addStretch(1)
        excel_layout.addLayout(self._build_output_dir_row(excel_tab, default_output_dir))
        self.tabs.addTab(excel_tab, "엑셀 보고서 출력")

        pdf_tab = QWidget(self)
        pdf_tab.setObjectName("reportExportPanel")
        pdf_layout = QVBoxLayout(pdf_tab)
        pdf_layout.setContentsMargins(12, 12, 12, 12)
        self.pdf_type_combo = QComboBox(pdf_tab)
        self.pdf_type_combo.addItem("조사보고서", "inspection")
        self.pdf_type_combo.addItem("보수후보고서", "post_repair")
        self.pdf_type_combo.addItem("비교보고서", "comparison")
        self._configure_export_combo(self.pdf_type_combo, minimum_width=220)
        pdf_layout.addWidget(
            self._build_export_form_row(pdf_tab, "보고서 종류", self.pdf_type_combo)
        )

        self.before_report_combo = QComboBox(pdf_tab)
        self.after_report_combo = QComboBox(pdf_tab)
        for report_id, label in report_options:
            self.before_report_combo.addItem(label, report_id)
            self.after_report_combo.addItem(label, report_id)
        self._configure_export_combo(self.before_report_combo, minimum_width=620)
        self._configure_export_combo(self.after_report_combo, minimum_width=620)
        self._select_combo_report(self.before_report_combo, current_report_id)
        self._select_first_other_report(self.after_report_combo, current_report_id)

        self.before_report_row = self._build_export_form_row(
            pdf_tab, "보수전 보고서", self.before_report_combo
        )
        self.after_report_row = self._build_export_form_row(
            pdf_tab, "보수후 보고서", self.after_report_combo
        )
        pdf_layout.addWidget(self.before_report_row)
        pdf_layout.addWidget(self.after_report_row)
        pdf_layout.addStretch(1)
        pdf_layout.addLayout(self._build_output_dir_row(pdf_tab, default_output_dir))
        self.tabs.addTab(pdf_tab, "PDF 종합보고서 출력")

        self.pdf_type_combo.currentIndexChanged.connect(self._update_pdf_report_controls)
        self.excel_type_combo.currentIndexChanged.connect(self._update_excel_report_controls)
        self.excel_select_all_checkbox.stateChanged.connect(self._toggle_excel_report_selection)
        self.excel_report_list.itemChanged.connect(self._sync_excel_select_all_state)
        self._update_excel_report_controls()
        self._update_pdf_report_controls()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("출력")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _configure_export_combo(
        self, combo: QComboBox, *, minimum_width: int
    ) -> None:
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setMinimumWidth(minimum_width)
        combo.setMinimumContentsLength(12)
        combo.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        max_text_width = minimum_width
        metrics = combo.fontMetrics()
        for index in range(combo.count()):
            max_text_width = max(
                max_text_width,
                metrics.horizontalAdvance(combo.itemText(index)) + 56,
            )
        combo.view().setMinimumWidth(max_text_width)

    def _build_export_form_row(
        self, parent: QWidget, label_text: str, field: QWidget
    ) -> QWidget:
        wrapper = QWidget(parent)
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        label = QLabel(label_text, wrapper)
        label.setFixedWidth(92)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)
        row.addWidget(field)
        row.addStretch(1)
        return wrapper

    def _build_export_control_row(self, parent: QWidget, control: QWidget) -> QWidget:
        wrapper = QWidget(parent)
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(control)
        row.addStretch(1)
        return wrapper

    def _update_excel_report_controls(self) -> None:
        self.excel_select_all_checkbox.setVisible(True)
        self.excel_report_list.setVisible(True)
        show_photos_option = self.excel_report_type() == "internal_defect"
        self.include_internal_photos_row.setVisible(show_photos_option)
        self.include_internal_photos_checkbox.setVisible(show_photos_option)
        filename = self.excel_filename_examples.get(self.excel_report_type(), "")
        self.excel_filename_label.setText(f"파일명 예시: {filename}" if filename else "")

    def _toggle_excel_report_selection(self, state: int) -> None:
        if self._syncing_excel_report_checks:
            return
        self._syncing_excel_report_checks = True
        try:
            checked = Qt.CheckState(state) == Qt.CheckState.Checked
            for row in range(self.excel_report_list.count()):
                self.excel_report_list.item(row).setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        finally:
            self._syncing_excel_report_checks = False

    def _sync_excel_select_all_state(self, _item: QListWidgetItem) -> None:
        if self._syncing_excel_report_checks:
            return
        self._set_excel_select_all_state_from_items()

    def _set_excel_select_all_state_from_items(self) -> None:
        self._syncing_excel_report_checks = True
        try:
            total = self.excel_report_list.count()
            checked = sum(
                1
                for row in range(total)
                if self.excel_report_list.item(row).checkState() == Qt.CheckState.Checked
            )
            if checked == total and total > 0:
                state = Qt.CheckState.Checked
            else:
                state = Qt.CheckState.Unchecked
            self.excel_select_all_checkbox.setCheckState(state)
        finally:
            self._syncing_excel_report_checks = False

    def _build_output_dir_row(
        self, parent, default_output_dir: str
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("출력 경로", parent))
        row.addWidget(self._build_output_dir_widget(parent, default_output_dir), 1)
        return row

    def _build_output_dir_widget(self, parent, default_output_dir: str) -> QWidget:
        wrapper = QWidget(parent)
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        edit = QLineEdit(default_output_dir, wrapper)
        edit.setMinimumWidth(420)
        edit.textChanged.connect(
            lambda text, source=edit: self._sync_output_dir(text, source)
        )
        self.output_dir_inputs.append(edit)
        browse_button = QPushButton("찾기", wrapper)
        browse_button.clicked.connect(
            lambda _checked=False, source=edit: self._choose_output_dir(source)
        )
        row.addWidget(edit, 1)
        row.addWidget(browse_button)
        return wrapper

    def _sync_output_dir(self, text: str, source: QLineEdit) -> None:
        if self._syncing_output_dir:
            return
        self._syncing_output_dir = True
        try:
            for edit in self.output_dir_inputs:
                if edit is not source and edit.text() != text:
                    edit.setText(text)
        finally:
            self._syncing_output_dir = False

    def _select_combo_report(self, combo: QComboBox, report_id: int) -> None:
        for idx in range(combo.count()):
            if int(combo.itemData(idx)) == report_id:
                combo.setCurrentIndex(idx)
                return

    def _select_first_other_report(self, combo: QComboBox, report_id: int) -> None:
        for idx in range(combo.count()):
            if int(combo.itemData(idx)) != report_id:
                combo.setCurrentIndex(idx)
                return
        self._select_combo_report(combo, report_id)

    def _update_pdf_report_controls(self) -> None:
        is_comparison = self.pdf_report_type() == "comparison"
        self.before_report_row.setVisible(is_comparison)
        self.after_report_row.setVisible(is_comparison)

    def _choose_output_dir(self, source: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "출력 경로 선택",
            source.text().strip(),
        )
        if selected:
            source.setText(selected)

    def selected_export_kind(self) -> str:
        return "excel" if self.tabs.currentIndex() == 0 else "pdf"

    def excel_report_type(self) -> str:
        return str(self.excel_type_combo.currentData())

    def selected_excel_report_ids(self) -> list[int]:
        ids: list[int] = []
        for row in range(self.excel_report_list.count()):
            item = self.excel_report_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def include_internal_defect_photos(self) -> bool:
        return (
            self.excel_report_type() == "internal_defect"
            and self.include_internal_photos_checkbox.isChecked()
        )

    def accept(self) -> None:
        if (
            self.selected_export_kind() == "excel"
            and not self.selected_excel_report_ids()
        ):
            QMessageBox.warning(self, "보고서 출력", "출력할 보고서를 선택하세요")
            return
        super().accept()

    def output_dir(self) -> str:
        if not self.output_dir_inputs:
            return ""
        return self.output_dir_inputs[0].text().strip()

    def pdf_report_type(self) -> str:
        return str(self.pdf_type_combo.currentData())

    def before_report_id(self) -> int:
        return int(self.before_report_combo.currentData())

    def after_report_id(self) -> int:
        return int(self.after_report_combo.currentData())
