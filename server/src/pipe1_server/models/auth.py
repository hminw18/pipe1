"""Accounts, credentials, sessions and audit tables (docs/Web_Auth_Spec.md §3).

Every stored token is a SHA-256 digest (32 bytes); raw tokens never reach the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pipe1_server.models.base import Base, CreatedAt, UuidPrimaryKey

ROLES = ("platform_admin", "org_admin", "inspector")
SHA256_LEN = 32


def _token_hash_column(*, unique: bool = True) -> Mapped[bytes]:
    return mapped_column(LargeBinary, unique=unique)


def _sha256_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"octet_length({column}) = {SHA256_LEN}", name=f"{column}_sha256")


def _lowercase_email_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} = lower({column})", name=f"{column}_lowercase")


class Organization(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("status IN ('active', 'disabled')", name="status"),)

    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="active")


class User(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN {ROLES}", name="role"),
        CheckConstraint("status IN ('invited', 'active', 'disabled')", name="status"),
        # Platform admins operate the service and belong to no customer organization.
        CheckConstraint(
            "(role = 'platform_admin') = (organization_id IS NULL)", name="org_membership"
        ),
        _lowercase_email_check("email"),
        # Target for composite foreign keys that require a user from a given organization.
        UniqueConstraint("id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    email: Mapped[str] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default="invited")
    mfa_required: Mapped[bool] = mapped_column(server_default=text("false"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordCredential(Base):
    __tablename__ = "password_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    hash: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MfaTotp(Base):
    __tablename__ = "mfa_totp"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Encrypted with Cloud KMS before storage.
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last accepted TOTP time step; codes from this step or earlier are rejected.
    last_used_step: Mapped[int | None] = mapped_column(BigInteger)


class RecoveryCode(UuidPrimaryKey, Base):
    __tablename__ = "recovery_codes"
    __table_args__ = (
        UniqueConstraint("user_id", "code_hash"),
        _sha256_check("code_hash"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        _sha256_check("token_hash"),
        CheckConstraint("idle_expires_at <= absolute_expires_at", name="expiry_order"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[bytes] = _token_hash_column()
    # Last password (+ MFA) verification; sensitive actions require it to be recent.
    authenticated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginChallenge(UuidPrimaryKey, CreatedAt, Base):
    """Pre-auth state between a correct password and a correct MFA code."""

    __tablename__ = "login_challenges"
    __table_args__ = (
        _sha256_check("token_hash"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[bytes] = _token_hash_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(server_default=text("0"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(f"role IN {ROLES}", name="role"),
        CheckConstraint(
            "(role = 'platform_admin') = (organization_id IS NULL)", name="org_membership"
        ),
        _lowercase_email_check("email"),
        _sha256_check("token_hash"),
        # Re-sending revokes the previous invitation, so at most one is open per email.
        Index(
            "uq_invitations_open_email",
            "email",
            unique=True,
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    email: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[bytes] = _token_hash_column()
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordReset(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "password_resets"
    __table_args__ = (_sha256_check("token_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[bytes] = _token_hash_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailChange(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "email_changes"
    __table_args__ = (_lowercase_email_check("new_email"), _sha256_check("token_hash"))

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    new_email: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[bytes] = _token_hash_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginThrottle(Base):
    __tablename__ = "login_throttle"

    # e.g. "account:<email>" or "ip:<address>"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    failure_count: Mapped[int] = mapped_column(server_default=text("0"))
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(UuidPrimaryKey, CreatedAt, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
