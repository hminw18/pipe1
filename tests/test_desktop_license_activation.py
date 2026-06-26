from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pipe1_license_server.signing import EntitlementSigner, generate_private_key_b64
from sewerpipe_inspector.licensing.api_client import LicenseApiClientProtocol
from sewerpipe_inspector.licensing.device_identity import DeviceIdentity
from sewerpipe_inspector.licensing.entitlement import EntitlementVerifier
from sewerpipe_inspector.licensing.license_service import LicenseService
from sewerpipe_inspector.licensing.local_store import LocalLicenseStore


class FakeLicenseClient(LicenseApiClientProtocol):
    def __init__(self, signer: EntitlementSigner) -> None:
        self.signer = signer
        self.seen_activation_keys: list[str] = []
        self.validation_calls = 0

    def activate(
        self,
        *,
        license_key: str,
        device_id: str,
        device_name: str | None,
        os_name: str,
        os_version: str,
        app_version: str,
    ) -> dict:
        self.seen_activation_keys.append(license_key)
        now = datetime.now(UTC)
        entitlement = self.signer.sign(
            {
                "license_id": "lic_test",
                "license_key_id": "key_test",
                "organization_id": "org_test",
                "license_status": "active",
                "plan": "standard",
                "features": {
                    "local_report": True,
                    "excel_export": True,
                    "pdf_export": True,
                    "training_upload": False,
                    "ai_assist": False,
                },
                "ai_quota": {
                    "enabled": False,
                    "period": "monthly",
                    "limit": 0,
                    "used": 0,
                    "remaining": 0,
                    "reset_at": None,
                    "overage_policy": "block",
                },
                "expires_at": (now + timedelta(days=365)).isoformat(),
                "offline_grace_until": (now + timedelta(days=14)).isoformat(),
                "device_id": device_id,
                "issued_at": now.isoformat(),
            }
        )
        return {"activation_id": "act_test", "entitlement": entitlement}

    def validate(
        self,
        *,
        activation_id: str,
        device_id: str,
        app_version: str,
    ) -> dict:
        self.validation_calls += 1
        return {"status": "valid", "entitlement": None}


def test_desktop_activation_stores_signed_entitlement_without_raw_key(
    tmp_path: Path,
) -> None:
    private_key = generate_private_key_b64()
    signer = EntitlementSigner(private_key, "test-key")
    verifier = EntitlementVerifier({signer.key_id: signer.public_key_b64})
    client = FakeLicenseClient(signer)
    store = LocalLicenseStore(tmp_path / "license_state.json")
    device_identity = DeviceIdentity(tmp_path / "device_id")
    service = LicenseService(
        store=store,
        device_identity=device_identity,
        client=client,
        verifier=verifier,
        app_version="0.1.0",
    )

    result = service.activate("PIPE1-ABCD-EFGH-IJKL-MNOP")

    assert result.status == "active"
    assert result.features["local_report"] is True
    assert client.seen_activation_keys == ["PIPE1-ABCD-EFGH-IJKL-MNOP"]
    raw_state = (tmp_path / "license_state.json").read_text(encoding="utf-8")
    assert "PIPE1-ABCD-EFGH-IJKL-MNOP" not in raw_state
    assert "PIPE1-ABCD" in raw_state

    reloaded = service.current_status()
    assert reloaded.status == "active"
    assert reloaded.can_use_feature("excel_export")
    assert not reloaded.can_use_feature("training_upload")


def test_desktop_rejects_entitlement_for_other_device(tmp_path: Path) -> None:
    private_key = generate_private_key_b64()
    signer = EntitlementSigner(private_key, "test-key")
    verifier = EntitlementVerifier({signer.key_id: signer.public_key_b64})
    other_device_entitlement = signer.sign(
        {
            "license_id": "lic_test",
            "license_status": "active",
            "plan": "standard",
            "features": {"local_report": True},
            "expires_at": (datetime.now(UTC) + timedelta(days=365)).isoformat(),
            "offline_grace_until": (datetime.now(UTC) + timedelta(days=14)).isoformat(),
            "device_id": "pipe1-dev-other",
            "issued_at": datetime.now(UTC).isoformat(),
        }
    )
    store = LocalLicenseStore(tmp_path / "license_state.json")
    store.save_activation_state(
        activation_id="act_test",
        masked_license_key="PIPE1-ABCD",
        entitlement=other_device_entitlement,
    )
    device_identity = DeviceIdentity(tmp_path / "device_id")
    device_identity.get_or_create("pipe1-dev-current")
    service = LicenseService(
        store=store,
        device_identity=device_identity,
        client=FakeLicenseClient(signer),
        verifier=verifier,
        app_version="0.1.0",
    )

    status = service.current_status()

    assert status.status == "invalid"
    assert "device" in (status.reason or "")

