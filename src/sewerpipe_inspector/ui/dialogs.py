from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
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


class BusinessDialog(QDialog):
    def __init__(
        self,
        business_code: str = "",
        business_name: str = "",
        client: str = "",
        business_start_date: str = "",
        business_end_date: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("사업")
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
