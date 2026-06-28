from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sewerpipe_inspector.licensing.signing import (
    SignatureVerificationError,
    verify_entitlement_envelope,
)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class EntitlementVerifier:
    def __init__(self, public_keys: dict[str, str]) -> None:
        self.public_keys = public_keys

    def verify(self, envelope: dict[str, Any], *, expected_device_id: str) -> dict[str, Any]:
        try:
            payload = verify_entitlement_envelope(envelope, self.public_keys)
        except SignatureVerificationError as exc:
            raise ValueError(f"invalid entitlement signature: {exc}") from exc

        if payload.get("device_id") != expected_device_id:
            raise ValueError("entitlement device does not match this device")

        now = datetime.now(UTC)
        expires_at = _parse_datetime(payload.get("expires_at"))
        if expires_at is not None and now > expires_at:
            raise ValueError("license entitlement is expired")
        grace_until = _parse_datetime(payload.get("offline_grace_until"))
        if grace_until is not None and now > grace_until:
            raise ValueError("offline grace period has expired")
        if payload.get("license_status") != "active":
            raise ValueError("license is not active")
        return payload
