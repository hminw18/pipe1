"""HTTP-level tests for docs/Web_Auth_Spec.md §5-§8."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pipe1_server.models.auth import AuditEvent, AuthSession, LoginThrottle
from tests.api_support import (
    APP_ORIGIN,
    PASSWORD,
    Clock,
    create_user,
    csrf_headers,
    login,
    make_client,
    make_settings,
    totp_code,
)

LOGIN_FAILED = "이메일 또는 비밀번호가 올바르지 않습니다."


def _login_attempt(client: TestClient, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- Login ---------------------------------------------------------------------


def test_login_creates_session_and_me_returns_user(
    client: TestClient, owner_engine: Engine
) -> None:
    user = create_user(owner_engine)
    assert login(client, user) == {"mfa_required": False, "mfa_enrollment_required": False}
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == user.email
    assert body["csrf_token"]


def test_session_cookie_attributes(client: TestClient, owner_engine: Engine) -> None:
    user = create_user(owner_engine)
    response = _login_attempt(client, user.email, PASSWORD)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-pipe1_session=")
    for attribute in ("HttpOnly", "Secure", "Path=/", "SameSite=lax"):
        assert attribute.lower() in cookie.lower()


def test_email_is_normalized(client: TestClient, owner_engine: Engine) -> None:
    user = create_user(owner_engine)
    response = _login_attempt(client, f"  {user.email.upper()} ", PASSWORD)
    assert response.status_code == 200


@pytest.mark.parametrize("case", ["wrong_password", "unknown_email", "disabled", "org_disabled"])
def test_login_failures_are_indistinguishable(
    client: TestClient, owner_engine: Engine, case: str
) -> None:
    if case == "unknown_email":
        email, password = "nobody@example.com", PASSWORD
    else:
        user = create_user(
            owner_engine,
            status="disabled" if case == "disabled" else "active",
            org_status="disabled" if case == "org_disabled" else "active",
        )
        email = user.email
        password = "wrong password!!" if case == "wrong_password" else PASSWORD
    response = _login_attempt(client, email, password)
    assert response.status_code == 401
    assert response.json() == {"detail": LOGIN_FAILED}
    assert "set-cookie" not in response.headers


def test_failed_login_is_audited(client: TestClient, owner_engine: Engine) -> None:
    user = create_user(owner_engine)
    _login_attempt(client, user.email, "wrong password!!")
    with Session(owner_engine) as session:
        actions = set(
            session.scalars(select(AuditEvent.action).where(AuditEvent.actor_user_id == user.id))
        )
    assert "auth.login_failed" in actions


# --- Throttling ----------------------------------------------------------------


def test_delay_after_five_failures_then_lock_after_ten(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    user = create_user(owner_engine)
    for _ in range(5):
        assert _login_attempt(client, user.email, "wrong password!!").status_code == 401
    delayed = _login_attempt(client, user.email, PASSWORD)
    assert delayed.status_code == 429
    assert int(delayed.headers["retry-after"]) >= 1

    for _ in range(5):
        clock.advance(seconds=61)
        assert _login_attempt(client, user.email, "wrong password!!").status_code == 401
    clock.advance(seconds=61)
    locked = _login_attempt(client, user.email, PASSWORD)
    assert locked.status_code == 429

    clock.advance(minutes=15)
    assert _login_attempt(client, user.email, PASSWORD).status_code == 200


def test_unknown_email_is_throttled_like_a_real_account(client: TestClient) -> None:
    for _ in range(5):
        assert _login_attempt(client, "ghost@example.com", "whatever pass").status_code == 401
    assert _login_attempt(client, "ghost@example.com", "whatever pass").status_code == 429


def test_successful_login_clears_account_failures(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    user = create_user(owner_engine)
    for _ in range(4):
        _login_attempt(client, user.email, "wrong password!!")
    assert _login_attempt(client, user.email, PASSWORD).status_code == 200
    for _ in range(4):
        assert _login_attempt(client, user.email, "wrong password!!").status_code == 401


# --- CSRF, origin, content type, headers -------------------------------------------


def test_state_change_requires_csrf_token(client: TestClient, owner_engine: Engine) -> None:
    login(client, create_user(owner_engine))
    assert client.post("/api/auth/logout", json={}).status_code == 403
    bad = client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": "forged"})
    assert bad.status_code == 403
    assert client.post("/api/auth/logout", json={}, headers=csrf_headers(client)).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_csrf_token_is_bound_to_session(client: TestClient, owner_engine: Engine) -> None:
    user = create_user(owner_engine)
    login(client, user)
    first = csrf_headers(client)
    login(client, user)
    assert client.post("/api/auth/heartbeat", json={}, headers=first).status_code == 403


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://app.test"])
def test_state_change_requires_exact_origin(client: TestClient, origin: str | None) -> None:
    headers = {"Origin": origin} if origin else {}
    if origin is None:
        client.headers.pop("Origin")
    response = client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": "x"}, headers=headers
    )
    assert response.status_code == 403


def test_state_change_requires_json(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        content="email=a@example.com&password=x",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 415


def test_security_headers(client: TestClient) -> None:
    response = client.get("/api/healthz")
    headers = response.headers
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert "max-age=63072000" in headers["strict-transport-security"]
    csp = headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "media-src 'self' https://storage.googleapis.com" in csp
    assert "unsafe-inline" not in csp


# --- Timeouts --------------------------------------------------------------------


def test_idle_timeout(client: TestClient, owner_engine: Engine, clock: Clock) -> None:
    login(client, create_user(owner_engine))
    clock.advance(hours=2, seconds=1)
    assert client.get("/api/auth/me").status_code == 401


def test_activity_extends_idle_but_not_absolute(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    login(client, create_user(owner_engine))
    headers = csrf_headers(client)
    for _ in range(11):
        clock.advance(hours=1)
        assert client.post("/api/auth/heartbeat", json={}, headers=headers).status_code == 200
    clock.advance(hours=1)
    assert client.get("/api/auth/me").status_code == 401


# --- MFA -------------------------------------------------------------------------


def test_admin_must_enroll_totp_before_first_session(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    import pyotp

    admin = create_user(owner_engine, role="org_admin")
    assert login(client, admin) == {"mfa_required": False, "mfa_enrollment_required": True}
    assert client.get("/api/auth/me").status_code == 401

    start = client.post("/api/auth/mfa/enrollment", json={})
    assert start.status_code == 200
    secret = start.json()["secret"]
    assert start.json()["otpauth_uri"].startswith("otpauth://totp/")
    assert len(secret) == 32  # 160 bits

    code = pyotp.TOTP(secret).at(clock.now)
    confirm = client.post("/api/auth/mfa/enrollment/confirm", json={"code": code})
    assert confirm.status_code == 200
    assert len(confirm.json()["recovery_codes"]) == 10
    assert client.get("/api/auth/me").json()["mfa_enrolled"] is True


def test_totp_login_and_replay_rejection(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    user = create_user(owner_engine, with_totp=True)
    assert login(client, user)["mfa_required"] is True
    code = totp_code(user.totp_secret, clock)
    assert client.post("/api/auth/mfa", json={"code": code}).status_code == 200

    client.cookies.clear()
    login(client, user)
    replay = client.post("/api/auth/mfa", json={"code": code})
    assert replay.status_code == 401


def test_mfa_attempts_are_limited_per_challenge(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    user = create_user(owner_engine, with_totp=True)
    login(client, user)
    for _ in range(5):
        assert client.post("/api/auth/mfa", json={"code": "000000"}).status_code == 401
    # The challenge is now dead even for the right code.
    good = client.post("/api/auth/mfa", json={"code": totp_code(user.totp_secret, clock)})
    assert good.status_code == 401


def test_password_login_does_not_reset_mfa_failures(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    user = create_user(owner_engine, with_totp=True)
    login(client, user)
    for _ in range(5):
        assert client.post("/api/auth/mfa", json={"code": "000000"}).status_code == 401
    clock.advance(minutes=1)
    login(client, user)  # correct password again: must not clear the counter
    with Session(owner_engine) as session:
        count = session.scalar(
            select(LoginThrottle.failure_count).where(LoginThrottle.key == f"account:{user.email}")
        )
    assert count == 5
    assert client.post("/api/auth/mfa", json={"code": "000000"}).status_code == 401
    # The 6th failure imposes a delay, so the next guess is refused outright.
    assert client.post("/api/auth/mfa", json={"code": "000000"}).status_code == 429


def test_challenge_expires(client: TestClient, owner_engine: Engine, clock: Clock) -> None:
    user = create_user(owner_engine, with_totp=True)
    login(client, user)
    clock.advance(minutes=5, seconds=1)
    response = client.post("/api/auth/mfa", json={"code": totp_code(user.totp_secret, clock)})
    assert response.status_code == 401


def test_recovery_code_works_once(client: TestClient, owner_engine: Engine, clock: Clock) -> None:
    import pyotp

    admin = create_user(owner_engine, role="org_admin")
    login(client, admin)
    secret = client.post("/api/auth/mfa/enrollment", json={}).json()["secret"]
    codes = client.post(
        "/api/auth/mfa/enrollment/confirm", json={"code": pyotp.TOTP(secret).at(clock.now)}
    ).json()["recovery_codes"]

    client.cookies.clear()
    login(client, admin)
    assert client.post("/api/auth/mfa", json={"code": codes[0].lower()}).status_code == 200
    client.cookies.clear()
    login(client, admin)
    assert client.post("/api/auth/mfa", json={"code": codes[0]}).status_code == 401


# --- Sessions, re-authentication, password change ------------------------------------


def test_list_and_revoke_other_sessions(
    app_engine: Engine, owner_engine: Engine, clock: Clock
) -> None:
    user = create_user(owner_engine)
    settings = make_settings()
    with (
        make_client(app_engine, clock, settings) as laptop,
        make_client(app_engine, clock, settings) as phone,
    ):
        login(laptop, user)
        login(phone, user)
        sessions = laptop.get("/api/auth/sessions").json()
        assert len(sessions) == 2
        assert sum(s["current"] for s in sessions) == 1
        revoked = laptop.post(
            "/api/auth/sessions/revoke-others", json={}, headers=csrf_headers(laptop)
        )
        assert revoked.status_code == 204
        assert phone.get("/api/auth/me").status_code == 401
        assert laptop.get("/api/auth/me").status_code == 200


def test_cannot_revoke_someone_elses_session(
    app_engine: Engine, owner_engine: Engine, clock: Clock
) -> None:
    settings = make_settings()
    with (
        make_client(app_engine, clock, settings) as alice,
        make_client(app_engine, clock, settings) as bob,
    ):
        login(alice, create_user(owner_engine))
        login(bob, create_user(owner_engine))
        bob_session = bob.get("/api/auth/sessions").json()[0]["id"]
        response = alice.delete(f"/api/auth/sessions/{bob_session}", headers=csrf_headers(alice))
        assert response.status_code == 404
        assert bob.get("/api/auth/me").status_code == 200


def test_reauthentication_updates_authenticated_at(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    login(client, create_user(owner_engine))
    before = client.get("/api/auth/me").json()["authenticated_at"]
    clock.advance(minutes=30)
    headers = csrf_headers(client)
    wrong = client.post(
        "/api/auth/reauthenticate", json={"password": "nope nope nope"}, headers=headers
    )
    assert wrong.status_code == 401
    ok = client.post("/api/auth/reauthenticate", json={"password": PASSWORD}, headers=headers)
    assert ok.status_code == 200
    assert client.get("/api/auth/me").json()["authenticated_at"] > before


def test_reauthentication_requires_mfa_when_enrolled(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    user = create_user(owner_engine, with_totp=True)
    login(client, user)
    client.post("/api/auth/mfa", json={"code": totp_code(user.totp_secret, clock)})
    headers = csrf_headers(client)
    clock.advance(minutes=2)
    no_code = client.post("/api/auth/reauthenticate", json={"password": PASSWORD}, headers=headers)
    assert no_code.status_code == 401
    with_code = client.post(
        "/api/auth/reauthenticate",
        json={"password": PASSWORD, "code": totp_code(user.totp_secret, clock)},
        headers=headers,
    )
    assert with_code.status_code == 200


@pytest.mark.parametrize(
    "new_password",
    ["short pass", "x" * 129, "my pipe1 password is long"],
)
def test_password_policy(client: TestClient, owner_engine: Engine, new_password: str) -> None:
    login(client, create_user(owner_engine))
    response = client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": new_password},
        headers=csrf_headers(client),
    )
    assert response.status_code == 422


def test_password_containing_email_local_part_is_rejected(
    client: TestClient, owner_engine: Engine
) -> None:
    user = create_user(owner_engine)
    login(client, user)
    local_part = user.email.split("@")[0]
    response = client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": f"{local_part}-and-more"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 422


def test_password_change_revokes_other_sessions(
    app_engine: Engine, owner_engine: Engine, clock: Clock
) -> None:
    user = create_user(owner_engine)
    settings = make_settings()
    new_password = "a completely different passphrase"
    with (
        make_client(app_engine, clock, settings) as laptop,
        make_client(app_engine, clock, settings) as phone,
    ):
        login(laptop, user)
        login(phone, user)
        response = laptop.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": new_password},
            headers=csrf_headers(laptop),
        )
        assert response.status_code == 204
        assert phone.get("/api/auth/me").status_code == 401
        assert laptop.get("/api/auth/me").status_code == 200
        laptop.cookies.clear()
        assert login(laptop, user, new_password) == {
            "mfa_required": False,
            "mfa_enrollment_required": False,
        }


def test_wrong_current_password_blocks_change(client: TestClient, owner_engine: Engine) -> None:
    login(client, create_user(owner_engine))
    response = client.post(
        "/api/auth/password",
        json={"current_password": "not my password", "new_password": "another long passphrase"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 401


def test_session_token_is_stored_hashed(client: TestClient, owner_engine: Engine) -> None:
    user = create_user(owner_engine)
    login(client, user)
    raw = client.cookies.get("__Host-pipe1_session")
    with Session(owner_engine) as session:
        stored = session.scalars(
            select(AuthSession.token_hash).where(AuthSession.user_id == user.id)
        ).all()
    assert raw and all(raw.encode() not in digest for digest in stored)
    assert all(len(digest) == 32 for digest in stored)


def test_origin_constant_matches_settings() -> None:
    assert make_settings().app_origin == APP_ORIGIN


def test_form_body_is_rejected_even_without_json_header(client: TestClient) -> None:
    response = client.post("/api/auth/login", content="a=b", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415


def test_inspector_can_enable_and_disable_mfa(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    import pyotp

    user = create_user(owner_engine)
    login(client, user)
    headers = csrf_headers(client)
    secret = client.post("/api/auth/mfa/setup", json={}, headers=headers).json()["secret"]
    confirmed = client.post(
        "/api/auth/mfa/setup/confirm",
        json={"code": pyotp.TOTP(secret).at(clock.now)},
        headers=headers,
    )
    assert confirmed.status_code == 200 and len(confirmed.json()["recovery_codes"]) == 10
    client.cookies.clear()
    assert login(client, user)["mfa_required"] is True
    clock.advance(seconds=31)
    client.post("/api/auth/mfa", json={"code": pyotp.TOTP(secret).at(clock.now)})
    headers = csrf_headers(client)
    assert client.delete("/api/auth/mfa", headers=headers).status_code == 204
    client.cookies.clear()
    assert login(client, user)["mfa_required"] is False


def test_admin_cannot_disable_mfa(client: TestClient, owner_engine: Engine, clock: Clock) -> None:
    admin = create_user(owner_engine, role="org_admin", with_totp=True)
    login(client, admin)
    client.post("/api/auth/mfa", json={"code": totp_code(admin.totp_secret, clock)})
    assert client.delete("/api/auth/mfa", headers=csrf_headers(client)).status_code == 403


def test_taxonomy_lists_scores(client: TestClient, owner_engine: Engine) -> None:
    login(client, create_user(owner_engine))
    data = client.get("/api/taxonomy").json()
    pipe = next(c for c in data["categories"] if c["category"] == "관로")
    crack = next(d for d in pipe["defect_items"] if d["item"] == "균열(길이)")
    assert crack["defect_type"] in ("구조", "운영") and crack["scores"]
    assert "전체" in data["quadrants"] and "기타" in data["undriven_reasons"]
