from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from pipe1_license_server.admin import AdminService
from pipe1_license_server.app import create_app
from pipe1_license_server.settings import ServerSettings
from pipe1_license_server.signing import generate_private_key_b64
from sewerpipe_inspector.db import ACTUAL_SURVEY_FIELDS, MANHOLE_FIELDS, REPORT_FIELDS, Database
from sewerpipe_inspector.services.inspection_service import InspectionService
from sewerpipe_inspector.services.report_service import ReportService
from sewerpipe_inspector.services.storage_service import StorageService
from sewerpipe_inspector.services.training_upload_service import (
    TrainingUploadClient,
    TrainingUploadService,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _server(tmp_path: Path) -> tuple[TestClient, str, str, str]:
    settings = ServerSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'server.db'}",
        signing_private_key=generate_private_key_b64(),
        signing_key_id="test-key",
        app_env="test",
    )
    app = create_app(settings)
    admin = AdminService(settings)
    org_id = admin.create_organization("Training Co", None)
    license_id = admin.create_license(
        organization_id=org_id,
        plan="standard",
        device_limit=3,
        expires_at="2027-06-30T23:59:59Z",
        features={"training_upload": True, "local_report": True},
    )
    raw_key = admin.generate_license_key(license_id)
    client = TestClient(app)
    device_id = "pipe1-dev-training"
    activation = client.post(
        "/licenses/activate",
        json={
            "license_key": raw_key,
            "device_id": device_id,
            "device_name": "training-pc",
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert activation.status_code == 200, activation.text
    return client, license_id, device_id, activation.json()["device_upload_token"]


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
        {field: ("없음" if field.endswith("undriven_reason") else None) for field in ACTUAL_SURVEY_FIELDS},
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
    server_client, license_id, device_id, upload_token = _server(tmp_path)
    db = Database(tmp_path / "app.db")
    storage = StorageService(tmp_path / "workspace")
    training_upload = TrainingUploadService(
        db,
        client=TrainingUploadClient.from_test_client(
            server_client, upload_token=upload_token
        ),
        license_id=license_id,
        device_id=device_id,
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

    # Regenerating an unchanged report should not duplicate the snapshot.
    inspection.generate_excel_report(report_id)
    assert len(db.list_training_upload_snapshots(report_id)) == 1

    training_upload.process_pending_uploads()

    uploaded = db.list_training_upload_snapshots(report_id)[0]
    assert uploaded["status"] == "uploaded"
    assert uploaded["server_snapshot_id"]
    server_snapshot = server_client.get(
        f"/training/snapshots/{uploaded['server_snapshot_id']}",
        headers={"Authorization": f"Bearer {upload_token}"},
    )
    assert server_snapshot.status_code == 200
    body = server_snapshot.json()
    assert body["sample_count"] == 1
    assert body["samples"][0]["labels"]["defect_item"] == "균열(길이)"
