from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from sewerpipe_inspector.licensing.api_client import LicenseApiClientProtocol
from sewerpipe_inspector.licensing.device_identity import DeviceIdentity
from sewerpipe_inspector.licensing.dpapi import DpapiProtector
from sewerpipe_inspector.licensing.entitlement import EntitlementVerifier
from sewerpipe_inspector.licensing.license_service import LicenseService
from sewerpipe_inspector.licensing.local_store import LocalLicenseStore
from sewerpipe_inspector.licensing.signing import canonical_json_bytes


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class _EntitlementSigner:
    def __init__(self, key_id: str) -> None:
        self.key_id = key_id
        self._private_key = Ed25519PrivateKey.generate()

    @property
    def public_key_b64(self) -> str:
        public_key = self._private_key.public_key()
        raw = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
        return _b64url_encode(raw)

    def sign(self, payload: dict) -> dict:
        signature = self._private_key.sign(canonical_json_bytes(payload))
        return {
            "payload": payload,
            "signature": _b64url_encode(signature),
            "alg": "EdDSA",
            "kid": self.key_id,
        }


class FakeProtector:
    def protect(self, data: bytes) -> bytes:
        return data[::-1]

    def unprotect(self, protected_data: bytes) -> bytes:
        return protected_data[::-1]


class FakeLicenseClient(LicenseApiClientProtocol):
    def __init__(self, signer: _EntitlementSigner) -> None:
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
    signer = _EntitlementSigner("test-key")
    verifier = EntitlementVerifier({signer.key_id: signer.public_key_b64})
    client = FakeLicenseClient(signer)
    store = LocalLicenseStore(
        tmp_path / "license_state.json", protector=FakeProtector()
    )
    device_identity = DeviceIdentity(
        tmp_path / "device_id", protector=FakeProtector()
    )
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
    assert "PIPE1-ABCD" not in raw_state
    assert "pipe1.license_state.dpapi.v1" in raw_state

    reloaded = service.current_status()
    assert reloaded.status == "active"
    assert reloaded.can_use_feature("excel_export")
    assert not reloaded.can_use_feature("training_upload")


def test_desktop_rejects_entitlement_for_other_device(tmp_path: Path) -> None:
    signer = _EntitlementSigner("test-key")
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
    store = LocalLicenseStore(
        tmp_path / "license_state.json", protector=FakeProtector()
    )
    store.save_activation_state(
        activation_id="act_test",
        masked_license_key="PIPE1-ABCD",
        entitlement=other_device_entitlement,
    )
    device_identity = DeviceIdentity(
        tmp_path / "device_id", protector=FakeProtector()
    )
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


def test_plaintext_license_state_is_not_trusted(tmp_path: Path) -> None:
    state_file = tmp_path / "license_state.json"
    state_file.write_text(
        '{"activation_id":"act_copied","entitlement":{"payload":{}}}',
        encoding="utf-8",
    )
    store = LocalLicenseStore(state_file, protector=FakeProtector())

    assert store.load() is None


def test_dpapi_protector_roundtrip_when_available() -> None:
    if not DpapiProtector.is_available():
        return
    protector = DpapiProtector()
    protected = protector.protect(b"pipe1-secret-state")

    assert protected != b"pipe1-secret-state"
    assert protector.unprotect(protected) == b"pipe1-secret-state"


def test_device_identity_stores_random_id_in_protected_file(tmp_path: Path) -> None:
    device_file = tmp_path / "device_id"
    device_identity = DeviceIdentity(device_file, protector=FakeProtector())

    first = device_identity.get_or_create()
    second = device_identity.get_or_create()
    raw_file = device_file.read_text(encoding="utf-8")

    assert first == second
    assert first.startswith("pipe1-")
    assert first not in raw_file
    assert "pipe1.device_id.dpapi.v1" in raw_file


def test_plaintext_device_id_file_is_not_trusted(tmp_path: Path) -> None:
    device_file = tmp_path / "device_id"
    device_file.write_text("pipe1-copied-random-id", encoding="utf-8")
    device_identity = DeviceIdentity(device_file, protector=FakeProtector())

    device_id = device_identity.get_or_create()

    assert device_id != "pipe1-copied-random-id"
    assert device_id not in device_file.read_text(encoding="utf-8")


def test_device_identity_forced_value_is_stored_protected(tmp_path: Path) -> None:
    device_file = tmp_path / "device_id"
    device_identity = DeviceIdentity(device_file, protector=FakeProtector())

    assert device_identity.get_or_create("pipe1-forced-id") == "pipe1-forced-id"
    assert device_identity.get_or_create() == "pipe1-forced-id"
    assert "pipe1-forced-id" not in device_file.read_text(encoding="utf-8")
