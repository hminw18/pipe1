from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import cv2

from sewerpipe_inspector.db import Database
from sewerpipe_inspector.services.ocr_service import OCRService
from sewerpipe_inspector.services.report_service import DefectReportRow, ReportService
from sewerpipe_inspector.services.storage_service import StorageService

if TYPE_CHECKING:
    from sewerpipe_inspector.services.training_upload_service import TrainingUploadService


class InspectionService:
    def __init__(
        self,
        db: Database,
        storage: StorageService,
        report: ReportService,
        training_upload_service: "TrainingUploadService | None" = None,
    ) -> None:
        self.db = db
        self.storage = storage
        self.report = report
        self.training_upload_service = training_upload_service
        self.ocr = OCRService()
        self.logger = logging.getLogger(self.__class__.__name__)

    def register_or_replace_video(
        self,
        report_id: int,
        file_path: Path,
        recorded_date: Optional[str],
        duration: Optional[float],
        scan_direction: str,
    ) -> int:
        if not file_path.exists():
            self.logger.error("Video path does not exist: %s", file_path)
            raise FileNotFoundError(str(file_path))
        return self.db.upsert_video(
            report_id,
            str(file_path),
            duration,
            recorded_date,
            scan_direction,
        )

    def capture_and_save_defect(
        self,
        report_id: int,
        video_id: int,
        video_file: Path,
        frame,
        timestamp_ms: int,
        drive_direction: str,
        distance_m: float | None,
        item_category: Optional[str],
        condition_item: Optional[str],
        defect_item: Optional[str],
        grade: Optional[str],
        quadrant: Optional[str],
        manhole_defect_depth_m: float | None,
        memo: Optional[str],
    ) -> int:
        context = self.db.get_report_context(report_id)
        if context is None:
            raise ValueError("Invalid report id")
        video_row = self.db.get_video_by_id(video_id)
        if video_row is None or int(video_row["report_id"]) != report_id:
            raise ValueError("Invalid report video")

        capture_path = self.storage.capture_path(
            project_name=context["project_name"],
            business_code=context["business_code"],
            business_name=context["business_name"],
            report_number=context["report_number"],
            pipe_number=context["pipe_number"],
            video_filename=video_file.name,
            timestamp_ms=timestamp_ms,
            version_name=context["version_name"],
        )

        if not self._write_frame_capture(capture_path, frame):
            self.logger.error("Failed to save frame capture to %s", capture_path)
            raise RuntimeError("Frame capture save failed")

        resolved_distance = distance_m
        if resolved_distance is None:
            resolved_distance = self._extract_distance(video_row, frame)
        if resolved_distance is None:
            raise ValueError("distance_m is required")

        try:
            return self.db.create_defect(
                report_id=report_id,
                video_id=video_id,
                timestamp_ms=timestamp_ms,
                image_path=str(capture_path),
                drive_direction=drive_direction,
                distance_m=resolved_distance,
                item_category=item_category,
                condition_item=condition_item,
                defect_item=defect_item,
                grade=grade,
                quadrant=quadrant,
                manhole_defect_depth_m=manhole_defect_depth_m,
                memo=memo,
            )
        except Exception:
            capture_path.unlink(missing_ok=True)
            raise

    def _write_frame_capture(self, path: Path, frame) -> bool:
        suffix = path.suffix or ".png"
        try:
            ok, encoded = cv2.imencode(suffix, frame)
        except cv2.error:
            return False
        if not ok:
            return False
        try:
            path.write_bytes(encoded.tobytes())
        except OSError:
            return False
        return True

    def read_distance_for_frame(self, video_id: int, frame) -> Optional[float]:
        video_row = self.db.get_video_by_id(video_id)
        if video_row is None:
            return None
        return self._extract_distance(video_row, frame)

    def _extract_distance(self, video_row, frame) -> Optional[float]:
        roi_x = video_row["depth_roi_x"]
        roi_y = video_row["depth_roi_y"]
        roi_w = video_row["depth_roi_w"]
        roi_h = video_row["depth_roi_h"]
        if None in (roi_x, roi_y, roi_w, roi_h):
            return None

        raw_distance = self.ocr.read_depth_value(frame, (roi_x, roi_y, roi_w, roi_h))
        if raw_distance is None:
            return None
        return abs(float(raw_distance))

    def delete_defect_with_image(self, defect_id: int) -> None:
        defect = self.db.get_defect(defect_id)
        if defect is None:
            return
        image_path = Path(defect["image_path"])
        self.db.delete_defect(defect_id)
        if image_path.exists():
            image_path.unlink(missing_ok=True)

    def delete_project_with_artifacts(self, project_id: int) -> None:
        project = self.db.get_project(project_id)
        if project is None:
            return
        artifact_paths = {self.storage.project_root(project["project_name"])}
        for business in self.db.list_businesses(project_id):
            for report in self.db.list_reports(int(business["id"])):
                artifact_paths.update(self._report_artifact_paths(int(report["id"])))
        self.db.delete_project(project_id)
        self._remove_artifact_paths(artifact_paths)

    def delete_business_with_artifacts(self, business_id: int) -> None:
        business = self.db.get_business(business_id)
        if business is None:
            return
        project = self.db.get_project(int(business["project_id"]))
        artifact_paths: set[Path] = set()
        if project is not None:
            artifact_paths.add(
                self.storage.business_root(
                    project["project_name"],
                    business["business_code"],
                    business["business_name"],
                )
            )
        for report in self.db.list_reports(business_id):
            artifact_paths.update(self._report_artifact_paths(int(report["id"])))
        self.db.delete_business(business_id)
        self._remove_artifact_paths(artifact_paths)

    def delete_report_with_artifacts(self, report_id: int) -> None:
        artifact_paths = self._report_artifact_paths(report_id)
        self.db.delete_report(report_id)
        self._remove_artifact_paths(artifact_paths)

    def delete_report_version_with_artifacts(self, report_id: int) -> int:
        artifact_paths = self._report_version_artifact_paths(report_id)
        next_report_id = self.db.delete_report_version(report_id)
        self._remove_artifact_paths(artifact_paths)
        return next_report_id

    def create_report_version_from_latest(self, version_group_id: int) -> int:
        new_report_id = self.db.create_report_version_from_latest(version_group_id)
        self._copy_version_capture_artifacts(new_report_id)
        return new_report_id

    def _copy_version_capture_artifacts(self, new_report_id: int) -> None:
        context = self.db.get_report_context(new_report_id)
        video = self.db.get_video(new_report_id)
        if context is None or video is None:
            return
        video_file = Path(video["file_path"])
        for defect in self.db.list_defects(new_report_id):
            source = Path(defect["image_path"])
            if not source.exists():
                continue
            target = self.storage.capture_path(
                project_name=context["project_name"],
                business_code=context["business_code"],
                business_name=context["business_name"],
                report_number=context["report_number"],
                pipe_number=context["pipe_number"],
                video_filename=video_file.name,
                timestamp_ms=int(defect["timestamp_ms"]),
                version_name=context["version_name"],
            )
            if source.resolve() != target.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            self.db.update_defect_image_path(int(defect["id"]), str(target))

    def _report_artifact_paths(self, report_id: int) -> set[Path]:
        artifact_paths: set[Path] = set()
        context = self.db.get_report_context(report_id)
        if context is not None:
            artifact_paths.add(
                self.storage.report_root(
                    context["project_name"],
                    context["business_code"],
                    context["business_name"],
                    context["report_number"],
                    context["pipe_number"],
                )
            )
        for defect in self.db.list_defects(report_id):
            image_path = Path(defect["image_path"])
            if image_path.parent.name == "captures":
                artifact_paths.add(image_path.parent.parent)
        return artifact_paths

    def _report_version_artifact_paths(self, report_id: int) -> set[Path]:
        artifact_paths: set[Path] = set()
        context = self.db.get_report_context(report_id)
        if context is not None and context["version_name"]:
            artifact_paths.add(
                self.storage.report_version_root(
                    context["project_name"],
                    context["business_code"],
                    context["business_name"],
                    context["report_number"],
                    context["pipe_number"],
                    context["version_name"],
                )
            )
        for defect in self.db.list_defects(report_id):
            image_path = Path(defect["image_path"])
            if image_path.exists():
                artifact_paths.add(image_path)
        return artifact_paths

    def _remove_artifact_paths(self, artifact_paths: set[Path]) -> None:
        for path in sorted(artifact_paths, key=lambda item: len(item.parts)):
            if not path.exists():
                continue
            if path.is_file():
                path.unlink(missing_ok=True)
                continue
            self.storage.remove_tree(path)

    def generate_excel_report(
        self, report_id: int, report_path: Path | None = None
    ) -> Path:
        context = self.db.get_report_context(report_id)
        report = self.db.get_report(report_id)
        pipe_info = self.db.get_pipe_information(report_id)
        upstream = self.db.get_manhole(report_id, "upstream")
        downstream = self.db.get_manhole(report_id, "downstream")
        actual = self.db.get_actual_survey(report_id)
        if context is None or report is None:
            raise ValueError("Invalid report id")

        if report_path is None:
            report_path = self.storage.excel_report_path(
                context["project_name"],
                context["business_code"],
                context["business_name"],
                context["report_number"],
                context["pipe_number"],
                context["version_name"],
            )

        defects = self.db.list_defects(report_id)
        defect_payload: list[DefectReportRow] = [
            {
                "timestamp_ms": row["timestamp_ms"],
                "drive_direction": row["drive_direction"],
                "distance_m": row["distance_m"],
                "item_category": row["item_category"],
                "condition_item": row["condition_item"],
                "defect_item": row["defect_item"],
                "grade": row["grade"],
                "quadrant": row["quadrant"],
                "manhole_defect_depth_m": row["manhole_defect_depth_m"],
                "memo": row["memo"],
                "image_path": row["image_path"],
            }
            for row in defects
        ]

        generated_path = self.report.generate_inspection_report(
            report_path=report_path,
            context=context,
            report=report,
            pipe_info=pipe_info,
            upstream_manhole=upstream,
            downstream_manhole=downstream,
            actual_survey=actual,
            defects=defect_payload,
        )
        if self.training_upload_service is not None:
            self.training_upload_service.queue_report_snapshot(report_id, "excel")
        return generated_path
