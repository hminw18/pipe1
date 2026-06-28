from __future__ import annotations

import copy

import pytest

from sewerpipe_inspector.licensing.entitlement import EntitlementVerifier


SERVER_PUBLIC_KEY = "O2onvM62pC1io6jQKm8Nc2UyFXcd4kOmOsBIoYtZ2ik"
SERVER_SIGNED_ENVELOPE = {
    "alg": "EdDSA",
    "kid": "contract-key",
    "payload": {
        "ai_quota": {
            "enabled": False,
            "limit": 0,
            "overage_policy": "block",
            "period": "monthly",
            "remaining": 0,
            "reset_at": None,
            "used": 0,
        },
        "device_id": "pipe1-contract-device",
        "expires_at": "2099-12-31T23:59:59+00:00",
        "features": {
            "local_report": True,
            "training_upload": True,
        },
        "issued_at": "2026-06-28T00:00:00+00:00",
        "license_id": "lic_contract",
        "license_key_id": "key_contract",
        "license_status": "active",
        "offline_grace_until": "2099-12-31T23:59:59+00:00",
        "organization_id": "org_contract",
        "plan": "standard",
    },
    "signature": (
        "Ig2adzmjFgicqnPFeDXNIRZynEGMlRy-8YpgCg2uC5x2utkRXKdjaaABbXfWL332"
        "dv52pLpXEn_0hJKDkqnDBw"
    ),
}


def test_desktop_verifies_server_signed_entitlement_fixture() -> None:
    verifier = EntitlementVerifier({"contract-key": SERVER_PUBLIC_KEY})

    verified = verifier.verify(
        copy.deepcopy(SERVER_SIGNED_ENVELOPE),
        expected_device_id="pipe1-contract-device",
    )

    assert verified == SERVER_SIGNED_ENVELOPE["payload"]


def test_desktop_rejects_tampered_server_entitlement_fixture() -> None:
    verifier = EntitlementVerifier({"contract-key": SERVER_PUBLIC_KEY})
    envelope = copy.deepcopy(SERVER_SIGNED_ENVELOPE)
    envelope["payload"] = {
        **envelope["payload"],
        "license_status": "active",
        "plan": "enterprise",
    }

    with pytest.raises(ValueError, match="invalid entitlement signature"):
        verifier.verify(envelope, expected_device_id="pipe1-contract-device")
