"""Invitations, password reset, email change and administration (docs/Web_Auth_Spec.md §9)."""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pipe1_server.email.delivery import InMemoryQueue
from pipe1_server.models.auth import PasswordReset, User
from tests.api_support import (
    PASSWORD,
    Clock,
    TestUser,
    create_user,
    csrf_headers,
    login,
    make_client,
    make_settings,
    token_from_link,
    totp_code,
)

NEW_PASSWORD = "brand new long passphrase"


def _admin_client(client: TestClient, owner_engine: Engine, clock: Clock) -> TestUser:
    """Log `client` in as a fresh org admin with TOTP enrolled."""
    admin = create_user(owner_engine, role="org_admin", with_totp=True)
    login(client, admin)
    assert (
        client.post("/api/auth/mfa", json={"code": totp_code(admin.totp_secret, clock)}).status_code
        == 200
    )
    return admin


def _invite(client: TestClient, email: str, role: str = "inspector"):
    return client.post(
        "/api/admin/invitations",
        json={"email": email, "role": role},
        headers=csrf_headers(client),
    )


# --- Invitations -----------------------------------------------------------------


def test_invite_accept_and_login(
    client: TestClient,
    owner_engine: Engine,
    clock: Clock,
    outbox: InMemoryQueue,
    app_engine: Engine,
) -> None:
    _admin_client(client, owner_engine, clock)
    assert _invite(client, "New.Person@Example.com").status_code == 201
    message = outbox.messages[-1]
    assert message.template == "invitation" and message.to == "new.person@example.com"
    token = token_from_link(message.data["link"])
    assert message.data["link"].startswith("https://app.test/invite#token=")

    with make_client(app_engine, clock, make_settings(), outbox) as invitee:
        preview = invitee.post("/api/auth/invitations/preview", json={"token": token})
        assert preview.json()["email"] == "new.person@example.com"
        accepted = invitee.post(
            "/api/auth/invitations/accept", json={"token": token, "password": NEW_PASSWORD}
        )
        assert accepted.status_code == 200
        assert invitee.get("/api/auth/me").json()["role"] == "inspector"
        # Single use.
        again = invitee.post(
            "/api/auth/invitations/accept", json={"token": token, "password": NEW_PASSWORD}
        )
        assert again.status_code == 400


def test_invitation_expires(
    client: TestClient, owner_engine: Engine, clock: Clock, outbox: InMemoryQueue
) -> None:
    _admin_client(client, owner_engine, clock)
    _invite(client, "late@example.com")
    token = token_from_link(outbox.messages[-1].data["link"])
    clock.advance(hours=72, seconds=1)
    response = client.post(
        "/api/auth/invitations/accept", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 400


def test_resend_revokes_previous_invitation(
    client: TestClient, owner_engine: Engine, clock: Clock, outbox: InMemoryQueue
) -> None:
    _admin_client(client, owner_engine, clock)
    invitation_id = _invite(client, "twice@example.com").json()["id"]
    first = token_from_link(outbox.messages[-1].data["link"])
    resent = client.post(
        f"/api/admin/invitations/{invitation_id}/resend", json={}, headers=csrf_headers(client)
    )
    assert resent.status_code == 200
    assert client.post("/api/auth/invitations/preview", json={"token": first}).status_code == 400


def test_cannot_invite_existing_account(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    _admin_client(client, owner_engine, clock)
    existing = create_user(owner_engine)
    response = _invite(client, existing.email)
    assert response.status_code == 409
    assert response.json()["detail"] == "초대할 수 없는 이메일입니다."


def test_inspector_cannot_invite(client: TestClient, owner_engine: Engine) -> None:
    login(client, create_user(owner_engine))
    assert _invite(client, "x@example.com").status_code == 403


def test_invited_org_admin_signs_in_without_mfa(
    client: TestClient,
    owner_engine: Engine,
    clock: Clock,
    outbox: InMemoryQueue,
    app_engine: Engine,
) -> None:
    _admin_client(client, owner_engine, clock)
    _invite(client, "second-admin@example.com", role="org_admin")
    token = token_from_link(outbox.messages[-1].data["link"])
    with make_client(app_engine, clock, make_settings(), outbox) as invitee:
        accepted = invitee.post(
            "/api/auth/invitations/accept", json={"token": token, "password": NEW_PASSWORD}
        )
        assert accepted.json() == {"mfa_required": False, "mfa_enrollment_required": False}
        assert invitee.get("/api/auth/me").json()["role"] == "org_admin"


def test_bad_invitation_tokens_are_rate_limited_per_ip(client: TestClient) -> None:
    statuses = [
        client.post("/api/auth/invitations/preview", json={"token": f"guess-{i}"}).status_code
        for i in range(35)
    ]
    assert statuses[:20] == [400] * 20
    assert 429 in statuses


# --- Password reset ----------------------------------------------------------------


def test_password_reset_flow(
    client: TestClient,
    owner_engine: Engine,
    outbox: InMemoryQueue,
    app_engine: Engine,
    clock: Clock,
) -> None:
    user = create_user(owner_engine)
    with make_client(app_engine, clock, make_settings(), outbox) as other_device:
        login(other_device, user)
        response = client.post("/api/auth/password-reset", json={"email": user.email})
        assert response.status_code == 202
        reset = outbox.messages[-1]
        assert reset.template == "password_reset"
        token = token_from_link(reset.data["link"])

        done = client.post(
            "/api/auth/password-reset/complete", json={"token": token, "new_password": NEW_PASSWORD}
        )
        assert done.status_code == 204
        assert outbox.messages[-1].template == "password_changed"
        # Never logged in automatically; existing sessions revoked.
        assert client.get("/api/auth/me").status_code == 401
        assert other_device.get("/api/auth/me").status_code == 401
    assert login(client, user, NEW_PASSWORD)["mfa_required"] is False
    reuse = client.post(
        "/api/auth/password-reset/complete", json={"token": token, "new_password": "x" * 20}
    )
    assert reuse.status_code == 400


def test_reset_response_is_identical_for_unknown_email(
    client: TestClient, owner_engine: Engine, outbox: InMemoryQueue
) -> None:
    known = client.post("/api/auth/password-reset", json={"email": create_user(owner_engine).email})
    unknown = client.post("/api/auth/password-reset", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [m.to for m in outbox.messages] == [
        m.to for m in outbox.messages if m.to != "nobody@example.com"
    ]


def test_new_reset_request_invalidates_older_token(
    client: TestClient, owner_engine: Engine, outbox: InMemoryQueue
) -> None:
    user = create_user(owner_engine)
    client.post("/api/auth/password-reset", json={"email": user.email})
    first = token_from_link(outbox.messages[-1].data["link"])
    client.post("/api/auth/password-reset", json={"email": user.email})
    response = client.post(
        "/api/auth/password-reset/complete", json={"token": first, "new_password": NEW_PASSWORD}
    )
    assert response.status_code == 400


def test_reset_token_expires(
    client: TestClient, owner_engine: Engine, outbox: InMemoryQueue, clock: Clock
) -> None:
    user = create_user(owner_engine)
    client.post("/api/auth/password-reset", json={"email": user.email})
    token = token_from_link(outbox.messages[-1].data["link"])
    clock.advance(minutes=30, seconds=1)
    response = client.post(
        "/api/auth/password-reset/complete", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert response.status_code == 400


def test_reset_requests_are_rate_limited_silently(
    client: TestClient, owner_engine: Engine, outbox: InMemoryQueue
) -> None:
    user = create_user(owner_engine)
    for _ in range(8):
        assert (
            client.post("/api/auth/password-reset", json={"email": user.email}).status_code == 202
        )
    assert len([m for m in outbox.messages if m.to == user.email]) == 5


def test_password_change_invalidates_reset_tokens(
    client: TestClient, owner_engine: Engine, outbox: InMemoryQueue
) -> None:
    user = create_user(owner_engine)
    client.post("/api/auth/password-reset", json={"email": user.email})
    token = token_from_link(outbox.messages[-1].data["link"])
    login(client, user)
    client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=csrf_headers(client),
    )
    assert outbox.messages[-1].template == "password_changed"
    with Session(owner_engine) as session:
        open_resets = session.scalars(
            select(PasswordReset).where(
                PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)
            )
        ).all()
    assert open_resets == []
    response = client.post(
        "/api/auth/password-reset/complete", json={"token": token, "new_password": "x" * 20}
    )
    assert response.status_code == 400


# --- Email change -------------------------------------------------------------------


def test_email_change_flow(
    client: TestClient, owner_engine: Engine, outbox: InMemoryQueue, clock: Clock
) -> None:
    user = create_user(owner_engine)
    login(client, user)
    response = client.post(
        "/api/auth/email-change",
        json={"new_email": "moved@example.com"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 202
    confirm = outbox.messages[-1]
    assert confirm.to == "moved@example.com" and confirm.template == "email_change_confirm"
    done = client.post(
        "/api/auth/email-change/confirm", json={"token": token_from_link(confirm.data["link"])}
    )
    assert done.status_code == 204
    notice = outbox.messages[-1]
    assert notice.to == user.email and notice.template == "email_changed_notice"
    assert "moved@example.com" not in notice.data["new_email_masked"]
    assert client.get("/api/auth/me").json()["email"] == "moved@example.com"


def test_email_change_requires_recent_authentication(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    login(client, create_user(owner_engine))
    clock.advance(minutes=11)
    response = client.post(
        "/api/auth/email-change", json={"new_email": "x@example.com"}, headers=csrf_headers(client)
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "reauthentication_required"


def test_email_change_to_taken_address_sends_nothing(
    client: TestClient, owner_engine: Engine, outbox: InMemoryQueue
) -> None:
    taken = create_user(owner_engine)
    login(client, create_user(owner_engine))
    response = client.post(
        "/api/auth/email-change", json={"new_email": taken.email}, headers=csrf_headers(client)
    )
    assert response.status_code == 202
    assert all(m.to != taken.email for m in outbox.messages)


# --- Administration ----------------------------------------------------------------


def test_deactivation_revokes_sessions_immediately(
    client: TestClient,
    owner_engine: Engine,
    clock: Clock,
    outbox: InMemoryQueue,
    app_engine: Engine,
) -> None:
    _admin_client(client, owner_engine, clock)
    _invite(client, "member@example.com")
    token = token_from_link(outbox.messages[-1].data["link"])
    with make_client(app_engine, clock, make_settings(), outbox) as member:
        member.post("/api/auth/invitations/accept", json={"token": token, "password": NEW_PASSWORD})
        member_id = member.get("/api/auth/me").json()["id"]
        response = client.post(
            f"/api/admin/users/{member_id}/deactivate", json={}, headers=csrf_headers(client)
        )
        assert response.status_code == 204
        assert member.get("/api/auth/me").status_code == 401


def test_admin_cannot_touch_other_org_users(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    _admin_client(client, owner_engine, clock)
    outsider = create_user(owner_engine)
    response = client.post(
        f"/api/admin/users/{outsider.id}/deactivate", json={}, headers=csrf_headers(client)
    )
    assert response.status_code == 404
    with Session(owner_engine) as session:
        assert session.get(User, outsider.id).status == "active"


def test_role_change_requires_recent_auth_and_keeps_one_admin(
    client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    admin = _admin_client(client, owner_engine, clock)
    headers = csrf_headers(client)
    demote_self = client.put(
        f"/api/admin/users/{admin.id}/role", json={"role": "inspector"}, headers=headers
    )
    assert demote_self.status_code == 409
    clock.advance(minutes=11)
    stale = client.put(
        f"/api/admin/users/{admin.id}/role", json={"role": "inspector"}, headers=headers
    )
    assert stale.status_code == 403


def test_org_admin_resets_inspector_mfa_only(
    client: TestClient, owner_engine: Engine, clock: Clock, outbox: InMemoryQueue
) -> None:
    admin = _admin_client(client, owner_engine, clock)
    with Session(owner_engine) as session, session.begin():
        inspector = User(
            organization_id=admin.organization_id,
            email="mfa-user@example.com",
            role="inspector",
            status="active",
        )
        peer = User(
            organization_id=admin.organization_id,
            email="peer-admin@example.com",
            role="org_admin",
            status="active",
        )
        session.add_all([inspector, peer])
        session.flush()
        inspector_id, peer_id = inspector.id, peer.id
    headers = csrf_headers(client)
    ok = client.post(f"/api/admin/users/{inspector_id}/mfa-reset", json={}, headers=headers)
    assert ok.status_code == 204
    assert outbox.messages[-1].template == "mfa_reset_notice"
    denied = client.post(f"/api/admin/users/{peer_id}/mfa-reset", json={}, headers=headers)
    assert denied.status_code == 403


def test_org_audit_log_is_scoped(client: TestClient, owner_engine: Engine, clock: Clock) -> None:
    admin = _admin_client(client, owner_engine, clock)
    events = client.get("/api/admin/audit-events").json()
    assert events
    assert {e["organization_id"] for e in events} == {str(admin.organization_id)}


# --- Platform ---------------------------------------------------------------------


@pytest.fixture
def platform_client(app_engine: Engine, owner_engine: Engine, clock: Clock, outbox: InMemoryQueue):
    admin = create_user(owner_engine, role="platform_admin", with_totp=True)
    with make_client(app_engine, clock, make_settings(), outbox) as platform:
        login(platform, admin)
        platform.post("/api/auth/mfa", json={"code": totp_code(admin.totp_secret, clock)})
        yield platform


def test_platform_admin_creates_organization_with_admin_invite(
    platform_client: TestClient, outbox: InMemoryQueue
) -> None:
    response = platform_client.post(
        "/api/platform/organizations",
        json={"name": "새 회사", "admin_email": "boss@newco.example"},
        headers=csrf_headers(platform_client),
    )
    assert response.status_code == 201
    invite = outbox.messages[-1]
    assert invite.to == "boss@newco.example"
    assert invite.data["organization_name"] == "새 회사"


def test_organization_deactivation_ends_member_sessions(
    platform_client: TestClient, client: TestClient, owner_engine: Engine, clock: Clock
) -> None:
    admin = _admin_client(client, owner_engine, clock)
    response = platform_client.post(
        f"/api/platform/organizations/{admin.organization_id}/deactivate",
        json={},
        headers=csrf_headers(platform_client),
    )
    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    # Members of the disabled organization can no longer log in.
    failed = client.post("/api/auth/login", json={"email": admin.email, "password": PASSWORD})
    assert failed.status_code == 401


def test_platform_admin_cannot_use_org_admin_api(platform_client: TestClient) -> None:
    assert platform_client.get("/api/admin/users").status_code == 403


def test_invited_platform_admin_must_enroll_mfa(
    platform_client: TestClient,
    clock: Clock,
    outbox: InMemoryQueue,
    app_engine: Engine,
) -> None:
    platform_client.post(
        "/api/platform/invitations",
        json={"email": "ops3@pipe1.example"},
        headers=csrf_headers(platform_client),
    )
    token = token_from_link(outbox.messages[-1].data["link"])
    with make_client(app_engine, clock, make_settings(), outbox) as invitee:
        accepted = invitee.post(
            "/api/auth/invitations/accept", json={"token": token, "password": NEW_PASSWORD}
        )
        assert accepted.json() == {"mfa_required": False, "mfa_enrollment_required": True}
        assert invitee.get("/api/auth/me").status_code == 401
        secret = invitee.post("/api/auth/mfa/enrollment", json={}).json()["secret"]
        confirmed = invitee.post(
            "/api/auth/mfa/enrollment/confirm", json={"code": pyotp.TOTP(secret).at(clock.now)}
        )
        assert confirmed.status_code == 200
