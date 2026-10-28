from __future__ import annotations

import unicodedata


def normalize_email(email: str) -> str:
    """Trim, NFKC-normalize and lowercase, as stored in users.email."""
    return unicodedata.normalize("NFKC", email).strip().lower()
