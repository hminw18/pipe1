"""TOTP verification with replay protection, and encryption of stored secrets.

Secrets are 160-bit (RFC 4226 recommendation). Codes are accepted for the current
30-second step ±1, and a step is never accepted twice (docs/Web_Auth_Spec.md §5).
"""

from __future__ import annotations

import base64
import hmac
import os
import time
from typing import Protocol

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

STEP_SECONDS = 30
VALID_WINDOW = 1


def new_secret() -> str:
    return pyotp.random_base32(length=32)  # 32 base32 chars = 160 bits


def provisioning_uri(secret: str, email: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(
    secret: str, code: str, last_used_step: int | None, now: float | None = None
) -> int | None:
    """Return the matched time step, or None. Steps at or before last_used_step never match."""
    code = code.strip()
    if not (len(code) == 6 and code.isdigit()):
        return None
    totp = pyotp.TOTP(secret)
    step = current_step(now)
    for candidate in range(step - VALID_WINDOW, step + VALID_WINDOW + 1):
        if last_used_step is not None and candidate <= last_used_step:
            continue
        if hmac.compare_digest(totp.generate_otp(candidate), code):
            return candidate
    return None


class SecretCipher(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> bytes: ...


class KmsCipher:
    """Cloud KMS symmetric key (production). The API service account has encrypter/decrypter."""

    def __init__(self, key_name: str) -> None:
        from google.cloud import kms

        self._client = kms.KeyManagementServiceClient()
        self._key_name = key_name

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._client.encrypt(
            request={
                "name": self._key_name,
                "plaintext": plaintext,
                "additional_authenticated_data": b"pipe1-totp",
            }
        ).ciphertext

    def decrypt(self, ciphertext: bytes) -> bytes:
        return self._client.decrypt(
            request={
                "name": self._key_name,
                "ciphertext": ciphertext,
                "additional_authenticated_data": b"pipe1-totp",
            }
        ).plaintext


class LocalAesGcmCipher:
    """AES-256-GCM with a key from settings. Dev and tests only; production uses Cloud KMS."""

    _NONCE_BYTES = 12

    def __init__(self, key_b64: str) -> None:
        key = base64.b64decode(key_b64)
        if len(key) != 32:
            raise ValueError("local TOTP key must be 32 bytes, base64-encoded")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(self._NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, plaintext, b"pipe1-totp")

    def decrypt(self, ciphertext: bytes) -> bytes:
        nonce, body = ciphertext[: self._NONCE_BYTES], ciphertext[self._NONCE_BYTES :]
        return self._aead.decrypt(nonce, body, b"pipe1-totp")
