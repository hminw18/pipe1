"""HMAC-signed synchronizer CSRF tokens (docs/Web_Auth_Spec.md §7).

The token is derived from the session id, so nothing is stored and it survives reloads;
a new session (login) always gets a new token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid


def csrf_token(key: str, session_id: uuid.UUID) -> str:
    mac = hmac.new(key.encode("utf-8"), b"pipe1-csrf:" + session_id.bytes, hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode("ascii")


def csrf_token_valid(key: str, session_id: uuid.UUID, provided: str | None) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(csrf_token(key, session_id), provided)
