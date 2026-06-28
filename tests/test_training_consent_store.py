from __future__ import annotations

from pathlib import Path

from sewerpipe_inspector.db import Database


def test_local_training_consent_record_can_be_accepted_and_revoked(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "app.db")

    db.set_training_upload_consent(
        license_id="lic_test",
        device_id="pipe1-dev",
        consent_type="capture_images_and_labels",
        consent_version="2026-06-25",
        accepted=True,
        app_version="0.1.0",
    )
    consent = db.get_training_upload_consent("lic_test", "pipe1-dev")
    assert consent is not None
    assert consent["accepted"] == 1
    assert consent["consent_version"] == "2026-06-25"

    db.set_training_upload_consent(
        license_id="lic_test",
        device_id="pipe1-dev",
        consent_type="capture_images_and_labels",
        consent_version="2026-06-25",
        accepted=False,
        app_version="0.1.0",
    )
    revoked = db.get_training_upload_consent("lic_test", "pipe1-dev")
    assert revoked is not None
    assert revoked["accepted"] == 0
    assert revoked["revoked_at"] is not None
