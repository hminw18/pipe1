"""Helpers for HTTP-level tests: a controllable clock, users, and a TestClient wired to the
test database as the app role."""

from __future__ import annotations

import base64
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from pipe1_server.api.deps import get_email_queue, get_now
from pipe1_server.db import get_session_factory
from pipe1_server.email.delivery import InMemoryQueue
from pipe1_server.ids import uuid7
from pipe1_server.main import create_app
from pipe1_server.models.auth import MfaTotp, Organization, PasswordCredential, User
from pipe1_server.security import totp
from pipe1_server.security.passwords import hash_password
from pipe1_server.security.totp import LocalAesGcmCipher
from pipe1_server.settings import Settings, get_settings

APP_ORIGIN = "https://app.test"
PASSWORD = "correct horse battery staple"
TOTP_LOCAL_KEY = base64.b64encode(os.urandom(32)).decode("ascii")
LOCAL_STORAGE_DIR = tempfile.mkdtemp(prefix="pipe1-test-storage-")


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "dev",
        "app_origin": APP_ORIGIN,
        "csrf_key": "test-csrf-key",
        "totp_local_key": TOTP_LOCAL_KEY,
        "local_storage_dir": LOCAL_STORAGE_DIR,
        "public_api_origin": "https://testserver",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


_ip_counter = iter(range(1, 1_000_000))


def next_test_ip() -> str:
    """A distinct documentation-range IPv6 address per client, so IP throttles don't collide."""
    return f"2001:db8::{next(_ip_counter):x}"


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def make_client(
    app_engine: Engine, clock: Clock, settings: Settings, outbox: InMemoryQueue | None = None
) -> TestClient:
    app = create_app(lambda: settings)
    outbox = outbox if outbox is not None else InMemoryQueue()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_now] = lambda: clock.now
    app.dependency_overrides[get_email_queue] = lambda: outbox
    app.dependency_overrides[get_session_factory] = lambda: sessionmaker(
        app_engine, expire_on_commit=False
    )
    return TestClient(
        app,
        base_url="https://testserver",
        headers={"Origin": APP_ORIGIN},
        client=(next_test_ip(), 50000),
    )


@dataclass(frozen=True)
class TestUser:
    id: uuid.UUID
    email: str
    organization_id: uuid.UUID | None
    totp_secret: str | None = None


def create_user(
    owner_engine: Engine,
    *,
    role: str = "inspector",
    status: str = "active",
    org_status: str = "active",
    with_totp: bool = False,
    password: str = PASSWORD,
) -> TestUser:
    """Create a user directly as the table owner (bypasses the API)."""
    with Session(owner_engine) as session, session.begin():
        organization_id = None
        if role != "platform_admin":
            organization = Organization(id=uuid7(), name="Test Co", status=org_status)
            session.add(organization)
            session.flush()
            organization_id = organization.id
        user = User(
            id=uuid7(),
            organization_id=organization_id,
            email=f"user-{uuid7().hex}@example.com",
            role=role,
            status=status,
        )
        session.add(user)
        session.flush()
        session.add(PasswordCredential(user_id=user.id, hash=hash_password(password)))
        secret = None
        if with_totp:
            secret = totp.new_secret()
            session.add(
                MfaTotp(
                    user_id=user.id,
                    secret_ciphertext=LocalAesGcmCipher(TOTP_LOCAL_KEY).encrypt(
                        secret.encode("ascii")
                    ),
                    confirmed_at=datetime.now(UTC),
                )
            )
        return TestUser(user.id, user.email, organization_id, secret)


def totp_code(secret: str, clock: Clock, offset_steps: int = 0) -> str:
    step = totp.current_step(clock.now.timestamp()) + offset_steps
    return pyotp.TOTP(secret).generate_otp(step)


def login(client: TestClient, user: TestUser, password: str = PASSWORD) -> dict[str, object]:
    response = client.post("/api/auth/login", json={"email": user.email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def csrf_headers(client: TestClient) -> dict[str, str]:
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    return {"X-CSRF-Token": me.json()["csrf_token"]}


def token_from_link(link: str) -> str:
    assert "#token=" in link
    return link.split("#token=", 1)[1]


def full_login(client: TestClient, user: TestUser, clock: Clock) -> None:
    """Password login plus TOTP when the user has it."""
    result = login(client, user)
    if result["mfa_required"]:
        assert user.totp_secret is not None
        response = client.post("/api/auth/mfa", json={"code": totp_code(user.totp_secret, clock)})
        assert response.status_code == 200, response.text
