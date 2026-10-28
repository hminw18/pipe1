"""Random tokens that are delivered once and stored only as SHA-256 digests."""

from __future__ import annotations

import hashlib
import secrets


def hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode("utf-8")).digest()


def new_token() -> tuple[str, bytes]:
    """Return a 256-bit URL-safe token and its digest for storage."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)
