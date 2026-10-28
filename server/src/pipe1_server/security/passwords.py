"""Password policy and hashing (docs/Web_Auth_Spec.md §4)."""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx2
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

MIN_LENGTH = 12
MAX_LENGTH = 128
FORBIDDEN_SUBSTRINGS = ("pipe1",)

# argon2-cffi defaults (t=3, m=64 MiB, p=4) exceed the OWASP minimum (t=2, m=19 MiB, p=1).
_hasher = PasswordHash((Argon2Hasher(),))


class PasswordPolicyError(ValueError):
    pass


def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def hash_password(password: str) -> str:
    return _hasher.hash(normalize_password(password))


def verify_password(password: str, stored_hash: str) -> tuple[bool, str | None]:
    """Return (valid, new_hash). new_hash is set when the stored hash should be upgraded."""
    return _hasher.verify_and_update(normalize_password(password), stored_hash)


@lru_cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(32))


def burn_verification_time(password: str) -> None:
    """Spend the same work as a real check, for logins with no matching account."""
    verify_password(password, _dummy_hash())


class BreachedPasswordChecker(Protocol):
    def is_breached(self, password: str) -> bool: ...


class HibpChecker:
    """Have I Been Pwned range API; only the first 5 hex chars of the SHA-1 leave the server."""

    def __init__(self, client: httpx2.Client | None = None) -> None:
        self._client = client or httpx2.Client(timeout=5.0)

    def is_breached(self, password: str) -> bool:
        digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()  # noqa: S324
        prefix, suffix = digest[:5], digest[5:]
        response = self._client.get(
            f"https://api.pwnedpasswords.com/range/{prefix}", headers={"Add-Padding": "true"}
        )
        response.raise_for_status()
        for line in response.text.splitlines():
            candidate, _, count = line.partition(":")
            if candidate == suffix and count.strip() != "0":
                return True
        return False


class OfflineListChecker:
    """Plain-text list of known-breached passwords, one per line."""

    def __init__(self, path: Path) -> None:
        with path.open(encoding="utf-8") as file:
            self._passwords = {line.rstrip("\r\n") for line in file if line.strip()}

    def is_breached(self, password: str) -> bool:
        return password in self._passwords


def validate_new_password(password: str, email: str, checker: BreachedPasswordChecker) -> str:
    """Return the normalized password, or raise PasswordPolicyError with a user-facing message."""
    normalized = normalize_password(password)
    if len(normalized) < MIN_LENGTH:
        raise PasswordPolicyError(f"비밀번호는 {MIN_LENGTH}자 이상이어야 합니다.")
    if len(normalized) > MAX_LENGTH:
        raise PasswordPolicyError(f"비밀번호는 {MAX_LENGTH}자 이하여야 합니다.")
    lowered = normalized.lower()
    local_part = email.split("@", 1)[0].lower()
    if (len(local_part) >= 3 and local_part in lowered) or any(
        word in lowered for word in FORBIDDEN_SUBSTRINGS
    ):
        raise PasswordPolicyError("이메일이나 서비스 이름이 포함된 비밀번호는 사용할 수 없습니다.")
    if checker.is_breached(normalized):
        raise PasswordPolicyError("유출된 적이 있는 비밀번호입니다. 다른 비밀번호를 사용하세요.")
    return normalized
