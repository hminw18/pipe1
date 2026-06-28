from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from sewerpipe_inspector.db import ACTUAL_SURVEY_FIELDS, MANHOLE_FIELDS, REPORT_FIELDS, Database
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.report_service import ReportService
from sewerpipe_inspector.services.storage_service import StorageService
from sewerpipe_inspector.services.training_upload_service import TrainingUploadService


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


class RecordingTrainingUploadClient:
    def __init__(self) -> None:
        self.consents: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.samples: list[dict[str, Any]] = []
        self.completed: list[str] = []
        self.upload_token: str | None = None

    def record_consent(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.consents.append(payload)
        return {"status": "ok"}

    def create_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.snapshots.append(payload)
        return {"snapshot_id": f"snap_{len(self.snapshots)}"}

    def upload_sample(self, snapshot_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.samples.append({"snapshot_id": snapshot_id, **payload})
        return {"sample_id": f"sample_{len(self.samples)}"}

    def complete_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        self.completed.append(snapshot_id)
        return {"status": "complete"}


def _make_report(db: Database, tmp_path: Path) -> int:
    project_id = db.create_project("Project")
    business_id = db.create_business(project_id, "B001", "Business", None, None, None)
    report_id = db.create_report(business_id, "R001", "PIPE-001")
    report = db.get_report(report_id)
    payload = {field: report[field] for field in REPORT_FIELDS}
    payload.update(
        {
            "survey_date": "2026-06-25",
            "buried_years": "10",
            "pipe_type": "PVC",
            "category": "관로",
            "specification": "D300",
        }
    )
    db.update_report(report_id, payload)
    db.update_manhole(
        report_id,
        "upstream",
        {field: ("MH-U" if field == "manhole_number" else None) for field in MANHOLE_FIELDS},
    )
    db.update_manhole(
        report_id,
        "downstream",
        {field: ("MH-D" if field == "manhole_number" else None) for field in MANHOLE_FIELDS},
    )
    db.update_pipe_information(report_id, 20.0, 20.0)
    db.update_actual_survey(
        report_id,
        {
            field: ("없음" if field.endswith("undriven_reason") else None)
            for field in ACTUAL_SURVEY_FIELDS
        },
    )
    capture = tmp_path / "capture.png"
    capture.write_bytes(PNG_1X1)
    video_id = db.upsert_video(report_id, str(tmp_path / "video.mp4"), 10.0, None, "순주행")
    db.create_defect(
        report_id,
        video_id,
        1200,
        str(capture),
        "순주행",
        1.25,
        "관로",
        None,
        "균열(길이)",
        "대",
        "상",
        None,
        "memo should not upload",
    )
    return report_id


def test_report_generation_queues_training_snapshot_and_uploads(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "app.db")
    storage = StorageService(tmp_path / "workspace")
    client = RecordingTrainingUploadClient()
    training_upload = TrainingUploadService(
        db,
        client=client,  # type: ignore[arg-type]
        license_id="lic_desktop",
        device_id="pipe1-dev-training",
        consent_enabled=True,
        consent_version="2026-06-25",
    )
    inspection = InspectionService(
        db,
        storage,
        ReportService(),
        training_upload_service=training_upload,
    )
    report_id = _make_report(db, tmp_path)

    inspection.generate_excel_report(report_id)

    snapshots = db.list_training_upload_snapshots(report_id)
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "pending"
    samples = db.list_training_upload_samples(int(snapshots[0]["id"]))
    assert len(samples) == 1
    assert "memo should not upload" not in samples[0]["payload_json"]

    inspection.generate_excel_report(report_id)
    assert len(db.list_training_upload_snapshots(report_id)) == 1

    training_upload.process_pending_uploads()

    uploaded = db.list_training_upload_snapshots(report_id)[0]
    assert uploaded["status"] == "uploaded"
    assert uploaded["server_snapshot_id"] == "snap_1"
    assert client.consents[0]["license_id"] == "lic_desktop"
    assert client.snapshots[0]["local_report_id"] == str(report_id)
    assert client.samples[0]["labels"]["defect_item"] == "균열(길이)"
    assert client.samples[0]["image_base64"]
    assert client.completed == ["snap_1"]
