from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class SignatureVerificationError(ValueError):
    pass


def verify_entitlement_envelope(
    envelope: dict[str, Any],
    public_keys: dict[str, str],
) -> dict[str, Any]:
    if envelope.get("alg") != "EdDSA":
        raise SignatureVerificationError("unsupported alg")
    kid = envelope.get("kid")
    if not isinstance(kid, str) or kid not in public_keys:
        raise SignatureVerificationError("unknown kid")
    payload = envelope.get("payload")
    signature = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise SignatureVerificationError("malformed entitlement")

    public_key = Ed25519PublicKey.from_public_bytes(_b64url_decode(public_keys[kid]))
    try:
        public_key.verify(_b64url_decode(signature), canonical_json_bytes(payload))
    except Exception as exc:
        raise SignatureVerificationError("invalid signature") from exc
    return payload
