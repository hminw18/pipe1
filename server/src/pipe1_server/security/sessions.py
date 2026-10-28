"""Server-side sessions (docs/Web_Auth_Spec.md §6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pipe1_server.models.auth import AuthSession, LoginChallenge, Organization, User
from pipe1_server.security.tokens import hash_token, new_token
from pipe1_server.settings import Settings

# Avoid a database write on every request; idle expiry moves forward at most once a minute.
TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass(frozen=True)
class ActiveSession:
    session: AuthSession
    user: User
    organization: Organization | None


def create_session(
    db: Session,
    *,
    user_id: uuid.UUID,
    now: datetime,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, AuthSession]:
    raw, digest = new_token()
    absolute = now + timedelta(seconds=settings.session_absolute_timeout_seconds)
    record = AuthSession(
        user_id=user_id,
        token_hash=digest,
        authenticated_at=now,
        last_seen_at=now,
        idle_expires_at=min(
            now + timedelta(seconds=settings.session_idle_timeout_seconds), absolute
        ),
        absolute_expires_at=absolute,
        ip=ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(record)
    db.flush()
    return raw, record


def load_active_session(db: Session, raw_token: str, now: datetime) -> ActiveSession | None:
    """Session for the cookie value if unexpired, unrevoked, and its user and org are active."""
    row = db.execute(
        select(AuthSession, User, Organization)
        .join(User, User.id == AuthSession.user_id)
        .outerjoin(Organization, Organization.id == User.organization_id)
        .where(AuthSession.token_hash == hash_token(raw_token))
    ).one_or_none()
    if row is None:
        return None
    record, user, organization = row
    if (
        record.revoked_at is not None
        or now >= record.idle_expires_at
        or now >= record.absolute_expires_at
        or user.status != "active"
        or (organization is not None and organization.status != "active")
    ):
        return None
    return ActiveSession(record, user, organization)


def touch(record: AuthSession, now: datetime, settings: Settings) -> None:
    """Extend the idle timeout; never past the absolute timeout."""
    if now - record.last_seen_at < TOUCH_INTERVAL:
        return
    record.last_seen_at = now
    record.idle_expires_at = min(
        now + timedelta(seconds=settings.session_idle_timeout_seconds), record.absolute_expires_at
    )


def revoke(record: AuthSession, now: datetime) -> None:
    record.revoked_at = now


def revoke_all_for_user(
    db: Session, user_id: uuid.UUID, now: datetime, *, except_session_id: uuid.UUID | None = None
) -> None:
    statement = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if except_session_id is not None:
        statement = statement.where(AuthSession.id != except_session_id)
    db.execute(statement)
    db.execute(
        update(LoginChallenge)
        .where(LoginChallenge.user_id == user_id, LoginChallenge.used_at.is_(None))
        .values(used_at=now)
    )


def is_recently_authenticated(record: AuthSession, now: datetime, settings: Settings) -> bool:
    return now - record.authenticated_at <= timedelta(
        seconds=settings.reauthentication_window_seconds
    )
