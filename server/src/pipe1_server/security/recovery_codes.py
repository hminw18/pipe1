"""MFA recovery codes: 10 per user, 80 bits each, shown once and stored as SHA-256."""

from __future__ import annotations

import hashlib
import secrets

CODE_COUNT = 10
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 symbols, no 0/O or 1/I
_SYMBOLS = 16  # 16 x 5 bits = 80 bits


def normalize(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def looks_like_recovery_code(code: str) -> bool:
    return len(normalize(code)) == _SYMBOLS


def code_hash(code: str) -> bytes:
    return hashlib.sha256(normalize(code).encode("ascii")).digest()


def generate() -> list[str]:
    """Return display-formatted codes, e.g. ABCD-EFGH-JKLM-NPQR."""
    codes = []
    for _ in range(CODE_COUNT):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(_SYMBOLS))
        codes.append("-".join(raw[i : i + 4] for i in range(0, _SYMBOLS, 4)))
    return codes
