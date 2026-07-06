from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from sewerpipe_inspector.db import Database


def _row_to_dict(row: Any | None, *, exclude: set[str] | None = None) -> dict[str, Any]:
    if row is None:
        return {}
    exclude = exclude or set()
    return {key: row[key] for key in row.keys() if key not in exclude}


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TrainingUploadClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        upload_token: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if base_url is not None:
            _validate_secure_base_url(base_url)
        self.base_url = base_url.rstrip("/") if base_url else None
        self.upload_token = upload_token
        self.timeout_seconds = timeout_seconds
        self._test_client: Any | None = None

    @classmethod
    def from_test_client(
        cls, client: Any, *, upload_token: str | None = None
    ) -> "TrainingUploadClient":
        instance = cls(upload_token=upload_token)
        instance._test_client = client
        return instance

    def create_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/training/snapshots", payload)

    def record_consent(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/training/consents", payload)

    def upload_sample(self, snapshot_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/training/snapshots/{snapshot_id}/samples", payload)

    def complete_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return self._post(f"/training/snapshots/{snapshot_id}/complete", {})

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._authorization_headers()
        if self._test_client is not None:
            response = self._test_client.post(path, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        if self.base_url is None:
            raise RuntimeError("training upload client base_url is not configured")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}{path}", json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    def _authorization_headers(self) -> dict[str, str]:
        if not self.upload_token:
            raise RuntimeError("training upload token is not configured")
        return {"Authorization": f"Bearer {self.upload_token}"}


class TrainingUploadService:
    def __init__(
        self,
        db: Database,
        *,
        client: TrainingUploadClient,
        license_id: str,
        device_id: str,
        consent_enabled: bool,
        upload_token: str | None = None,
        consent_type: str = "capture_images_and_labels",
        consent_version: str = "2026-06-25",
        app_version: str = "0.1.2",
    ) -> None:
        self.db = db
        self.client = client
        self.license_id = license_id
        self.device_id = device_id
        if upload_token is not None:
            self.client.upload_token = upload_token
        self.consent_enabled = consent_enabled
        self.consent_type = consent_type
        self.consent_version = consent_version
        self.app_version = app_version

    def queue_report_snapshot(self, report_id: int, export_type: str) -> int | None:
        consent = self.db.get_training_upload_consent(
            self.license_id, self.device_id, self.consent_type
        )
        if consent is not None:
            self.consent_enabled = bool(consent["accepted"])
            self.consent_version = str(consent["consent_version"])
        if not self.consent_enabled:
            return None
        payload, samples = self._build_report_payload(report_id, export_type)
        fingerprint = _canonical_hash(
            {
                "report": payload,
                "samples": [
                    {
                        "defect_id": sample["local_defect_id"],
                        "image_sha256": sample["image_sha256"],
                        "labels": sample["labels"],
                    }
                    for sample in samples
                ],
            }
        )
        snapshot_id = self.db.create_training_upload_snapshot(
            report_id=report_id,
            report_fingerprint=fingerprint,
            export_type=export_type,
            payload=payload,
            sample_count=len(samples),
        )
        existing_samples = self.db.list_training_upload_samples(snapshot_id)
        if existing_samples:
            return snapshot_id
        for sample in samples:
            self.db.create_training_upload_sample(
                snapshot_id=snapshot_id,
                defect_id=int(sample["local_defect_id"]),
                image_path=str(sample["image_path"]),
                image_sha256=str(sample["image_sha256"]),
                payload={
                    "local_defect_id": sample["local_defect_id"],
                    "image_sha256": sample["image_sha256"],
                    "image_filename": sample["image_filename"],
                    "labels": sample["labels"],
                    "metadata": sample["metadata"],
                },
            )
        return snapshot_id

    def process_pending_uploads(self) -> None:
        for snapshot in self.db.list_pending_training_upload_snapshots():
            snapshot_id = int(snapshot["id"])
            try:
                self.db.mark_training_upload_snapshot_uploading(snapshot_id)
                snapshot_payload = json.loads(snapshot["payload_json"])
                consent_payload = snapshot_payload.get("consent", {})
                self.client.record_consent(
                    {
                        "license_id": self.license_id,
                        "device_id": self.device_id,
                        "consent_type": consent_payload.get(
                            "type", self.consent_type
                        ),
                        "consent_version": consent_payload.get(
                            "version", self.consent_version
                        ),
                        "accepted": bool(consent_payload.get("accepted", False)),
                        "app_version": snapshot_payload.get(
                            "app_version", self.app_version
                        ),
                    }
                )
                created = self.client.create_snapshot(
                    {
                        "license_id": self.license_id,
                        "device_id": self.device_id,
                        "local_report_id": str(snapshot["report_id"]),
                        "report_fingerprint": snapshot["report_fingerprint"],
                        "export_type": snapshot["export_type"],
                        "metadata": snapshot_payload,
                    }
                )
                server_snapshot_id = str(created["snapshot_id"])
                for sample in self.db.list_training_upload_samples(snapshot_id):
                    sample_payload = json.loads(sample["payload_json"])
                    image_path = Path(sample["image_path"])
                    image_base64 = None
                    if image_path.exists():
                        image_base64 = base64.b64encode(image_path.read_bytes()).decode(
                            "ascii"
                        )
                    uploaded = self.client.upload_sample(
                        server_snapshot_id,
                        {
                            **sample_payload,
                            "image_base64": image_base64,
                        },
                    )
                    self.db.mark_training_upload_sample_uploaded(
                        int(sample["id"]), str(uploaded["sample_id"])
                    )
                self.client.complete_snapshot(server_snapshot_id)
                self.db.mark_training_upload_snapshot_uploaded(
                    snapshot_id, server_snapshot_id
                )
            except Exception as exc:
                self.db.mark_training_upload_snapshot_failed(snapshot_id, str(exc))
                raise

    def _build_report_payload(
        self, report_id: int, export_type: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        report = self.db.get_report(report_id)
        if report is None:
            raise ValueError("Invalid report id")
        pipe_info = self.db.get_pipe_information(report_id)

        payload = {
            "export_type": export_type,
            "local_report_id": str(report_id),
            "payload_schema_version": "training_snapshot.v1",
            "app_version": self.app_version,
            "consent": {
                "accepted": True,
                "type": self.consent_type,
                "version": self.consent_version,
            },
            "report_context": {
                "pipe_type": report["pipe_type"],
                "category": report["category"],
                "specification": report["specification"],
            },
            "pipe_information": {
                "length_m": pipe_info["length_m"] if pipe_info is not None else None,
                "total_drive_distance_m": pipe_info["total_drive_distance_m"]
                if pipe_info is not None
                else None,
                "is_completed": bool(pipe_info["is_completed"])
                if pipe_info is not None
                else False,
                "undriven_distance_m": pipe_info["undriven_distance_m"]
                if pipe_info is not None
                else None,
            },
        }

        samples: list[dict[str, Any]] = []
        for defect in self.db.list_defects(report_id):
            image_path = Path(defect["image_path"])
            if not image_path.exists():
                continue
            if not defect["condition_item"] and not defect["defect_item"]:
                continue
            image_sha256 = _file_sha256(image_path)
            sample_type = "defect" if defect["defect_item"] else "condition"
            labels = {
                "sample_type": sample_type,
                "drive_direction": defect["drive_direction"],
                "distance_m": defect["distance_m"],
                "item_category": defect["item_category"],
                "condition_item": defect["condition_item"],
                "defect_item": defect["defect_item"],
                "grade": defect["grade"],
                "quadrant": defect["quadrant"],
                "manhole_defect_depth_m": defect["manhole_defect_depth_m"],
                "pipe_type": report["pipe_type"],
                "category": report["category"],
                "specification": report["specification"],
            }
            metadata = {
                "timestamp_ms": defect["timestamp_ms"],
                "video_file_name": Path(defect["video_file_path"]).name,
            }
            samples.append(
                {
                    "local_defect_id": str(defect["id"]),
                    "image_path": image_path,
                    "image_sha256": image_sha256,
                    "image_filename": image_path.name,
                    "labels": labels,
                    "metadata": metadata,
                }
            )
        payload["sample_count"] = len(samples)
        return payload, samples


def _validate_secure_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("Production training upload base URL must use HTTPS.")
