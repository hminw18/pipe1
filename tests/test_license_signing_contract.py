from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pipe1_license_server.signing import EntitlementSigner, generate_private_key_b64
from sewerpipe_inspector.licensing.entitlement import EntitlementVerifier


def _active_payload(device_id: str) -> dict:
    now = datetime.now(UTC)
    return {
        "license_id": "lic_contract",
        "license_key_id": "key_contract",
        "organization_id": "org_contract",
        "license_status": "active",
        "plan": "standard",
        "features": {"local_report": True, "training_upload": True},
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


def test_desktop_verifies_server_signed_entitlement() -> None:
    signer = EntitlementSigner(generate_private_key_b64(), "contract-key")
    verifier = EntitlementVerifier({signer.key_id: signer.public_key_b64})
    payload = _active_payload("pipe1-contract-device")

    verified = verifier.verify(
        signer.sign(payload),
        expected_device_id="pipe1-contract-device",
    )

    assert verified == payload


def test_desktop_rejects_tampered_server_entitlement() -> None:
    signer = EntitlementSigner(generate_private_key_b64(), "contract-key")
    verifier = EntitlementVerifier({signer.key_id: signer.public_key_b64})
    envelope = signer.sign(_active_payload("pipe1-contract-device"))
    envelope["payload"] = {
        **envelope["payload"],
        "license_status": "active",
        "plan": "enterprise",
    }

    with pytest.raises(ValueError, match="invalid entitlement signature"):
        verifier.verify(envelope, expected_device_id="pipe1-contract-device")
