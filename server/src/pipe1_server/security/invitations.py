"""Invitation creation shared by org admins, platform admins and the bootstrap CLI."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pipe1_server.email.delivery import EmailMessage
from pipe1_server.models.auth import Invitation, Organization, User
from pipe1_server.security import audit
from pipe1_server.security.emails import normalize_email
from pipe1_server.security.tokens import new_token
from pipe1_server.settings import Settings

INVITATION_TTL = timedelta(hours=72)
CANNOT_INVITE = "초대할 수 없는 이메일입니다."


class InvitationError(ValueError):
    pass


def create_invitation(
    db: Session,
    *,
    email: str,
    role: str,
    organization_id: uuid.UUID | None,
    invited_by: uuid.UUID | None,
    now: datetime,
    settings: Settings,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Invitation, EmailMessage]:
    """Create an invitation (revoking this organization's previous open one for the email).

    Returns the invitation and the email to enqueue after commit.
    """
    email = normalize_email(email)
    if "@" not in email or len(email) > 320:
        raise InvitationError("이메일 형식을 확인하세요.")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise InvitationError(CANNOT_INVITE)
    open_elsewhere = db.scalar(
        select(Invitation.id).where(
            Invitation.email == email,
            Invitation.used_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
            Invitation.organization_id.is_distinct_from(organization_id),
        )
    )
    if open_elsewhere is not None:
        raise InvitationError(CANNOT_INVITE)
    db.execute(
        update(Invitation)
        .where(
            Invitation.email == email,
            Invitation.used_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    db.flush()

    raw, digest = new_token()
    invitation = Invitation(
        organization_id=organization_id,
        email=email,
        role=role,
        token_hash=digest,
        invited_by=invited_by,
        expires_at=now + INVITATION_TTL,
    )
    db.add(invitation)
    db.flush()
    audit.record(
        db,
        "admin.invitation_created",
        organization_id=organization_id,
        actor_user_id=invited_by,
        target_type="invitation",
        target_id=invitation.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"role": role},
    )
    organization = db.get(Organization, organization_id) if organization_id else None
    message = EmailMessage(
        to=email,
        template="invitation",
        data={
            "organization_name": organization.name if organization else "Pipe1 운영팀",
            "link": f"{settings.app_origin}/invite#token={raw}",
        },
    )
    return invitation, message
