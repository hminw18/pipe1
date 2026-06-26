from __future__ import annotations

import shutil
from pathlib import Path


class StorageService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize(name: str) -> str:
        bad = '<>:"/\\|?*'
        result = "".join("_" if ch in bad else ch for ch in name.strip())
        return result or "untitled"

    def project_root(self, project_name: str) -> Path:
        return self.workspace_root / self._sanitize(project_name)

    def business_root(
        self,
        project_name: str,
        business_code: str,
        business_name: str,
    ) -> Path:
        business_part = f"{business_code}_{business_name}".strip("_")
        return self.project_root(project_name) / self._sanitize(business_part)

    def report_root(
        self,
        project_name: str,
        business_code: str,
        business_name: str,
        report_number: str,
        pipe_number: str,
    ) -> Path:
        report_part = f"{report_number}_{pipe_number}".strip("_")
        return self.business_root(
            project_name, business_code, business_name
        ) / self._sanitize(report_part)

    def report_version_root(
        self,
        project_name: str,
        business_code: str,
        business_name: str,
        report_number: str,
        pipe_number: str,
        version_name: str | None = None,
    ) -> Path:
        root = self.report_root(
            project_name,
            business_code,
            business_name,
            report_number,
            pipe_number,
        )
        if version_name:
            return root / self._sanitize(version_name)
        return root

    def remove_tree(self, path: Path) -> None:
        target = path.resolve()
        root = self.workspace_root.resolve()
        if target == root or root not in target.parents:
            raise ValueError(f"Refusing to delete path outside workspace: {path}")
        if target.exists():
            shutil.rmtree(target)

    def ensure_report_dirs(
        self,
        project_name: str,
        business_code: str,
        business_name: str,
        report_number: str,
        pipe_number: str,
        version_name: str | None = None,
    ) -> tuple[Path, Path]:
        root = self.report_version_root(
            project_name,
            business_code,
            business_name,
            report_number,
            pipe_number,
            version_name,
        )
        captures = root / "captures"
        root.mkdir(parents=True, exist_ok=True)
        captures.mkdir(parents=True, exist_ok=True)
        return root, captures

    def capture_path(
        self,
        project_name: str,
        business_code: str,
        business_name: str,
        report_number: str,
        pipe_number: str,
        video_filename: str,
        timestamp_ms: int,
        version_name: str | None = None,
    ) -> Path:
        _, captures = self.ensure_report_dirs(
            project_name,
            business_code,
            business_name,
            report_number,
            pipe_number,
            version_name,
        )
        stem = Path(video_filename).stem
        return captures / f"{self._sanitize(stem)}_{timestamp_ms}.png"

    def excel_report_path(
        self,
        project_name: str,
        business_code: str,
        business_name: str,
        report_number: str,
        pipe_number: str,
        version_name: str | None = None,
    ) -> Path:
        root, _ = self.ensure_report_dirs(
            project_name,
            business_code,
            business_name,
            report_number,
            pipe_number,
            version_name,
        )
        suffix = f"_{self._sanitize(version_name)}" if version_name else ""
        return root / f"{self._sanitize(report_number)}{suffix}_InspectionReport.xlsx"

    def pdf_report_dir(
        self,
        project_name: str,
        business_code: str,
        business_name: str,
        report_number: str,
        pipe_number: str,
        version_name: str | None = None,
    ) -> Path:
        root, _ = self.ensure_report_dirs(
            project_name,
            business_code,
            business_name,
            report_number,
            pipe_number,
            version_name,
        )
        return root
