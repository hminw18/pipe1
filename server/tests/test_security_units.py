from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pyotp
import pytest
from cryptography.exceptions import InvalidTag

from pipe1_server.security import recovery_codes, totp
from pipe1_server.security.csrf import csrf_token, csrf_token_valid
from pipe1_server.security.emails import normalize_email
from pipe1_server.security.passwords import (
    OfflineListChecker,
    PasswordPolicyError,
    hash_password,
    validate_new_password,
    verify_password,
)
from pipe1_server.security.throttle import ACCOUNT_POLICY
from pipe1_server.security.totp import LocalAesGcmCipher
from pipe1_server.settings import Settings


class _NeverBreached:
    def is_breached(self, password: str) -> bool:
        return False


def test_totp_accepts_adjacent_steps_only() -> None:
    secret = totp.new_secret()
    now = 1_800_000_000.0
    step = totp.current_step(now)
    generator = pyotp.TOTP(secret)
    for offset in (-1, 0, 1):
        assert (
            totp.verify(secret, generator.generate_otp(step + offset), None, now) == step + offset
        )
    for offset in (-2, 2):
        assert totp.verify(secret, generator.generate_otp(step + offset), None, now) is None


def test_totp_rejects_used_and_earlier_steps() -> None:
    secret = totp.new_secret()
    now = 1_800_000_000.0
    step = totp.current_step(now)
    code = pyotp.TOTP(secret).generate_otp(step)
    assert totp.verify(secret, code, step, now) is None
    assert totp.verify(secret, code, step - 1, now) == step


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef"])
def test_totp_rejects_malformed_codes(code: str) -> None:
    assert totp.verify(totp.new_secret(), code, None) is None


def test_local_cipher_round_trip_and_tamper_detection() -> None:
    import base64
    import os

    cipher = LocalAesGcmCipher(base64.b64encode(os.urandom(32)).decode())
    ciphertext = cipher.encrypt(b"secret")
    assert cipher.decrypt(ciphertext) == b"secret"
    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    with pytest.raises(InvalidTag):
        cipher.decrypt(tampered)


def test_csrf_token_depends_on_key_and_session() -> None:
    session = uuid.uuid4()
    token = csrf_token("k1", session)
    assert csrf_token_valid("k1", session, token)
    assert not csrf_token_valid("k2", session, token)
    assert not csrf_token_valid("k1", uuid.uuid4(), token)
    assert not csrf_token_valid("k1", session, None)


def test_throttle_policy_schedule() -> None:
    assert [ACCOUNT_POLICY.lock_for(n) for n in range(1, 5)] == [None] * 4
    delays = [ACCOUNT_POLICY.lock_for(n) for n in range(5, 10)]
    assert delays == [timedelta(seconds=s) for s in (2, 4, 8, 16, 32)]
    assert ACCOUNT_POLICY.lock_for(10) == timedelta(minutes=15)


def test_password_hash_round_trip_with_nfkc() -> None:
    stored = hash_password("ｆｕｌｌｗｉｄｔｈ passphrase")
    assert stored.startswith("$argon2id$")
    assert verify_password("fullwidth passphrase", stored)[0]
    assert not verify_password("other passphrase!", stored)[0]


def test_offline_breached_list(tmp_path: Path) -> None:
    corpus = tmp_path / "breached.txt"
    corpus.write_text("password123456\nqwertyuiopas\n", encoding="utf-8")
    checker = OfflineListChecker(corpus)
    with pytest.raises(PasswordPolicyError):
        validate_new_password("qwertyuiopas", "a@example.com", checker)
    assert validate_new_password("unusual long passphrase", "a@example.com", checker)


def test_password_length_bounds() -> None:
    with pytest.raises(PasswordPolicyError):
        validate_new_password("elevenchars", "a@example.com", _NeverBreached())
    assert validate_new_password("twelve chars", "a@example.com", _NeverBreached())


def test_recovery_codes_format_and_entropy() -> None:
    codes = recovery_codes.generate()
    assert len(codes) == 10 and len(set(codes)) == 10
    for code in codes:
        assert len(code) == 19 and code.count("-") == 3
        assert recovery_codes.looks_like_recovery_code(code)
    assert recovery_codes.code_hash(codes[0].lower().replace("-", " ")) == recovery_codes.code_hash(
        codes[0]
    )


def test_email_normalization() -> None:
    assert normalize_email("  Ａlice@Example.COM ") == "alice@example.com"


def test_production_settings_require_real_secrets() -> None:
    with pytest.raises(ValueError, match="CSRF"):
        Settings(_env_file=None, environment="prod")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="APP_ORIGIN"):
        Settings(  # type: ignore[call-arg]
            _env_file=None, environment="prod", csrf_key="real", app_origin="http://x"
        )
