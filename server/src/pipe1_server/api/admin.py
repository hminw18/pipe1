"""Organization and platform administration (docs/Web_Auth_Spec.md §2, §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from pipe1_server.api.deps import (
    AppSettings,
    ClientIp,
    DbSession,
    EmailOutbox,
    Now,
    OrgAdmin,
    PlatformAdmin,
    UserAgent,
    require_recent_authentication,
    require_role,
)
from pipe1_server.db import after_commit
from pipe1_server.email.delivery import EmailMessage, EmailQueue
from pipe1_server.models.auth import (
    AuditEvent,
    Invitation,
    MfaTotp,
    Organization,
    RecoveryCode,
    User,
)
from pipe1_server.security import audit, throttle
from pipe1_server.security.invitations import InvitationError, create_invitation
from pipe1_server.security.sessions import ActiveSession, revoke_all_for_user

router = APIRouter(prefix="/api/admin", tags=["admin"])
platform_router = APIRouter(prefix="/api/platform", tags=["platform"])


def _recent_admin(*roles: str):
    role_guard = require_role(*roles)

    # Defaults rather than annotations: with postponed annotations FastAPI cannot resolve
    # names local to this function.
    def guard(
        active: ActiveSession = Depends(role_guard),  # noqa: B008
        _recent: ActiveSession = Depends(require_recent_authentication),  # noqa: B008
    ) -> ActiveSession:
        return active

    return guard


RecentOrgAdmin = Annotated[ActiveSession, Depends(_recent_admin("org_admin"))]
RecentPlatformAdmin = Annotated[ActiveSession, Depends(_recent_admin("platform_admin"))]


# --- Schemas -------------------------------------------------------------------


class UserSummary(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str
    mfa_enrolled: bool
    last_login_at: datetime | None
    created_at: datetime


class InvitationRequest(BaseModel):
    email: str = Field(max_length=320)
    role: Literal["inspector", "org_admin"]


class InvitationSummary(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    created_at: datetime


class RoleChange(BaseModel):
    role: Literal["inspector", "org_admin"]


class AuditEventOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    created_at: datetime


class OrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    admin_email: str = Field(max_length=320)


class OrganizationSummary(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime


class PlatformInvitationRequest(BaseModel):
    email: str = Field(max_length=320)


# --- Helpers -------------------------------------------------------------------


def _user_summary(db: Session, user: User) -> UserSummary:
    totp = db.get(MfaTotp, user.id)
    return UserSummary(
        id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        mfa_enrolled=totp is not None and totp.confirmed_at is not None,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _org_user(db: Session, admin: ActiveSession, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None or user.organization_id != admin.user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return user


def _invite(
    db: Session,
    queue: EmailQueue,
    *,
    admin: ActiveSession,
    email: str,
    role: str,
    organization_id: uuid.UUID | None,
    now: datetime,
    settings,
    ip: str | None,
    user_agent: str | None,
) -> Invitation:
    if not throttle.consume(db, f"invite:{admin.user.id}", throttle.INVITE_ADMIN_POLICY, now):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "초대 요청이 너무 많습니다.")
    try:
        invitation, message = create_invitation(
            db,
            email=email,
            role=role,
            organization_id=organization_id,
            invited_by=admin.user.id,
            now=now,
            settings=settings,
            ip=ip,
            user_agent=user_agent,
        )
    except InvitationError as error:
        audit.record(
            db,
            "admin.invitation_rejected",
            organization_id=organization_id,
            actor_user_id=admin.user.id,
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    after_commit(db, queue.enqueue, message)
    return invitation


def _invitation_summary(invitation: Invitation) -> InvitationSummary:
    return InvitationSummary(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


def _reset_mfa(
    db: Session,
    queue: EmailQueue,
    *,
    admin: ActiveSession,
    target: User,
    now: datetime,
    ip: str | None,
    user_agent: str | None,
) -> None:
    db.execute(delete(MfaTotp).where(MfaTotp.user_id == target.id))
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == target.id))
    revoke_all_for_user(db, target.id, now)
    audit.record(
        db,
        "admin.mfa_reset",
        organization_id=target.organization_id,
        actor_user_id=admin.user.id,
        target_type="user",
        target_id=target.id,
        ip=ip,
        user_agent=user_agent,
    )
    after_commit(db, queue.enqueue, EmailMessage(to=target.email, template="mfa_reset_notice"))


def _audit_page(db: Session, organization_id: uuid.UUID | None, limit: int) -> list[AuditEventOut]:
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    if organization_id is not None:
        query = query.where(AuditEvent.organization_id == organization_id)
    return [
        AuditEventOut(
            id=event.id,
            organization_id=event.organization_id,
            actor_user_id=event.actor_user_id,
            action=event.action,
            target_type=event.target_type,
            target_id=event.target_id,
            ip=str(event.ip) if event.ip is not None else None,
            created_at=event.created_at,
        )
        for event in db.scalars(query)
    ]


# --- Organization admin ------------------------------------------------------------


@router.get("/users", response_model=list[UserSummary])
def list_users(admin: OrgAdmin, db: DbSession) -> list[UserSummary]:
    users = db.scalars(
        select(User).where(User.organization_id == admin.user.organization_id).order_by(User.email)
    )
    return [_user_summary(db, user) for user in users]


@router.post("/invitations", response_model=InvitationSummary, status_code=201)
def invite_user(
    body: InvitationRequest,
    admin: OrgAdmin,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> InvitationSummary:
    invitation = _invite(
        db,
        queue,
        admin=admin,
        email=body.email,
        role=body.role,
        organization_id=admin.user.organization_id,
        now=now,
        settings=settings,
        ip=ip,
        user_agent=user_agent,
    )
    return _invitation_summary(invitation)


@router.get("/invitations", response_model=list[InvitationSummary])
def list_invitations(admin: OrgAdmin, db: DbSession, now: Now) -> list[InvitationSummary]:
    invitations = db.scalars(
        select(Invitation)
        .where(
            Invitation.organization_id == admin.user.organization_id,
            Invitation.used_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .order_by(Invitation.created_at.desc())
    )
    return [_invitation_summary(invitation) for invitation in invitations]


def _org_invitation(db: Session, admin: ActiveSession, invitation_id: uuid.UUID) -> Invitation:
    invitation = db.get(Invitation, invitation_id)
    if (
        invitation is None
        or invitation.organization_id != admin.user.organization_id
        or invitation.used_at is not None
        or invitation.revoked_at is not None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return invitation


@router.post("/invitations/{invitation_id}/resend", response_model=InvitationSummary)
def resend_invitation(
    invitation_id: uuid.UUID,
    admin: OrgAdmin,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> InvitationSummary:
    previous = _org_invitation(db, admin, invitation_id)
    invitation = _invite(
        db,
        queue,
        admin=admin,
        email=previous.email,
        role=previous.role,
        organization_id=admin.user.organization_id,
        now=now,
        settings=settings,
        ip=ip,
        user_agent=user_agent,
    )
    return _invitation_summary(invitation)


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(
    invitation_id: uuid.UUID,
    admin: OrgAdmin,
    db: DbSession,
    now: Now,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    invitation = _org_invitation(db, admin, invitation_id)
    invitation.revoked_at = now
    audit.record(
        db,
        "admin.invitation_revoked",
        organization_id=admin.user.organization_id,
        actor_user_id=admin.user.id,
        target_type="invitation",
        target_id=invitation.id,
        ip=ip,
        user_agent=user_agent,
    )


@router.post("/users/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_user(
    user_id: uuid.UUID,
    admin: OrgAdmin,
    db: DbSession,
    now: Now,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    target = _org_user(db, admin, user_id)
    if target.id == admin.user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "자기 자신은 비활성화할 수 없습니다.")
    target.status = "disabled"
    revoke_all_for_user(db, target.id, now)
    audit.record(
        db,
        "admin.user_deactivated",
        organization_id=admin.user.organization_id,
        actor_user_id=admin.user.id,
        target_type="user",
        target_id=target.id,
        ip=ip,
        user_agent=user_agent,
    )


@router.put("/users/{user_id}/role", response_model=UserSummary)
def change_role(
    user_id: uuid.UUID,
    body: RoleChange,
    admin: RecentOrgAdmin,
    db: DbSession,
    now: Now,
    ip: ClientIp,
    user_agent: UserAgent,
) -> UserSummary:
    target = _org_user(db, admin, user_id)
    if target.role == body.role:
        return _user_summary(db, target)
    if target.role == "org_admin":
        admins = db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == admin.user.organization_id,
                User.role == "org_admin",
                User.status == "active",
            )
        )
        if admins <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "마지막 관리자는 변경할 수 없습니다.")
    previous = target.role
    target.role = body.role
    revoke_all_for_user(db, target.id, now)
    audit.record(
        db,
        "admin.role_changed",
        organization_id=admin.user.organization_id,
        actor_user_id=admin.user.id,
        target_type="user",
        target_id=target.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"from": previous, "to": body.role},
    )
    return _user_summary(db, target)


@router.post("/users/{user_id}/mfa-reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_mfa(
    user_id: uuid.UUID,
    admin: RecentOrgAdmin,
    db: DbSession,
    now: Now,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    target = _org_user(db, admin, user_id)
    if target.role != "inspector":
        # Org admins' MFA is reset by platform admins (docs/Web_Auth_Spec.md §9.5).
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "관리자의 2단계 인증은 운영팀이 초기화합니다."
        )
    _reset_mfa(db, queue, admin=admin, target=target, now=now, ip=ip, user_agent=user_agent)


@router.get("/audit-events", response_model=list[AuditEventOut])
def org_audit_events(
    admin: OrgAdmin, db: DbSession, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[AuditEventOut]:
    return _audit_page(db, admin.user.organization_id, limit)


# --- Platform admin ----------------------------------------------------------------


@platform_router.get("/organizations", response_model=list[OrganizationSummary])
def list_organizations(admin: PlatformAdmin, db: DbSession) -> list[OrganizationSummary]:
    return [
        OrganizationSummary(id=o.id, name=o.name, status=o.status, created_at=o.created_at)
        for o in db.scalars(select(Organization).order_by(Organization.created_at.desc()))
    ]


@platform_router.post("/organizations", response_model=OrganizationSummary, status_code=201)
def create_organization(
    body: OrganizationRequest,
    admin: PlatformAdmin,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> OrganizationSummary:
    organization = Organization(name=body.name.strip())
    db.add(organization)
    db.flush()
    audit.record(
        db,
        "platform.organization_created",
        organization_id=organization.id,
        actor_user_id=admin.user.id,
        target_type="organization",
        target_id=organization.id,
        ip=ip,
        user_agent=user_agent,
    )
    _invite(
        db,
        queue,
        admin=admin,
        email=body.admin_email,
        role="org_admin",
        organization_id=organization.id,
        now=now,
        settings=settings,
        ip=ip,
        user_agent=user_agent,
    )
    return OrganizationSummary(
        id=organization.id,
        name=organization.name,
        status=organization.status,
        created_at=organization.created_at or now,
    )


@platform_router.post(
    "/organizations/{organization_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT
)
def deactivate_organization(
    organization_id: uuid.UUID,
    admin: RecentPlatformAdmin,
    db: DbSession,
    now: Now,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    organization.status = "disabled"
    for user_id in db.scalars(select(User.id).where(User.organization_id == organization_id)):
        revoke_all_for_user(db, user_id, now)
    db.execute(
        update(Invitation)
        .where(Invitation.organization_id == organization_id, Invitation.used_at.is_(None))
        .values(revoked_at=now)
    )
    audit.record(
        db,
        "platform.organization_deactivated",
        organization_id=organization_id,
        actor_user_id=admin.user.id,
        target_type="organization",
        target_id=organization_id,
        ip=ip,
        user_agent=user_agent,
    )


@platform_router.post("/invitations", response_model=InvitationSummary, status_code=201)
def invite_platform_admin(
    body: PlatformInvitationRequest,
    admin: RecentPlatformAdmin,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> InvitationSummary:
    invitation = _invite(
        db,
        queue,
        admin=admin,
        email=body.email,
        role="platform_admin",
        organization_id=None,
        now=now,
        settings=settings,
        ip=ip,
        user_agent=user_agent,
    )
    return _invitation_summary(invitation)


@platform_router.post("/users/{user_id}/mfa-reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_admin_mfa(
    user_id: uuid.UUID,
    admin: RecentPlatformAdmin,
    db: DbSession,
    now: Now,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    target = db.get(User, user_id)
    if target is None or target.role != "org_admin":
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _reset_mfa(db, queue, admin=admin, target=target, now=now, ip=ip, user_agent=user_agent)


@platform_router.get("/audit-events", response_model=list[AuditEventOut])
def platform_audit_events(
    admin: PlatformAdmin, db: DbSession, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[AuditEventOut]:
    return _audit_page(db, None, limit)
