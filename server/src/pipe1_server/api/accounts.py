"""Invitation acceptance, password reset and email change (docs/Web_Auth_Spec.md §9.2-§9.4).

Token links carry the token in the URL fragment (#token=...), which browsers never send to
servers, so tokens stay out of access logs and Referer headers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from pipe1_server.api.auth import (
    MAX_PASSWORD_INPUT,
    MFA_REQUIRED_ROLES,
    BreachedChecker,
    LoginResponse,
    _cookie_options,
    _start_session,
)
from pipe1_server.api.deps import (
    MFA_COOKIE,
    AppSettings,
    ClientIp,
    DbSession,
    EmailOutbox,
    Now,
    RecentlyAuthenticated,
    SessionFactory,
    UserAgent,
)
from pipe1_server.db import after_commit
from pipe1_server.email.delivery import EmailMessage, EmailQueue
from pipe1_server.email.templates import mask_email
from pipe1_server.models.auth import (
    EmailChange,
    Invitation,
    LoginChallenge,
    Organization,
    PasswordCredential,
    PasswordReset,
    User,
)
from pipe1_server.security import audit, throttle
from pipe1_server.security.emails import normalize_email
from pipe1_server.security.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_new_password,
)
from pipe1_server.security.sessions import revoke_all_for_user
from pipe1_server.security.tokens import hash_token, new_token
from pipe1_server.settings import Settings

router = APIRouter(prefix="/api/auth", tags=["accounts"])

INVITATION_INVALID = "초대가 유효하지 않거나 만료되었습니다."
LINK_INVALID = "링크가 유효하지 않거나 만료되었습니다."
RESET_ACCEPTED = "계정이 존재하면 재설정 메일이 발송됩니다."
RESET_TTL = timedelta(minutes=30)
EMAIL_CHANGE_TTL = timedelta(minutes=30)


class TokenRequest(BaseModel):
    token: str = Field(max_length=128)


class InvitationPreview(BaseModel):
    email: str
    organization_name: str | None
    role: str


class AcceptInvitationRequest(BaseModel):
    token: str = Field(max_length=128)
    password: str = Field(max_length=MAX_PASSWORD_INPUT)


class ResetRequest(BaseModel):
    email: str = Field(max_length=320)


class ResetCompleteRequest(BaseModel):
    token: str = Field(max_length=128)
    new_password: str = Field(max_length=MAX_PASSWORD_INPUT)


class EmailChangeRequest(BaseModel):
    new_email: str = Field(max_length=320)


class Accepted(BaseModel):
    message: str


def link(settings: Settings, path: str, token: str) -> str:
    return f"{settings.app_origin}{path}#token={token}"


def _enforce_token_rate(db: Session, ip: str | None, now: datetime) -> None:
    if ip is None:
        return
    until = throttle.locked_until(db, f"token-ip:{ip}", now)
    if until is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "시도 횟수가 너무 많습니다. 잠시 후 다시 시도하세요.",
            headers={"Retry-After": str(max(1, int((until - now).total_seconds())))},
        )


def _token_failure(db: Session, ip: str | None, now: datetime, detail: str) -> HTTPException:
    if ip is not None:
        throttle.record_failure(db, f"token-ip:{ip}", throttle.TOKEN_IP_POLICY, now)
        db.commit()
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail)


def _open_invitation(db: Session, token: str, now: datetime) -> Invitation | None:
    invitation = db.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(token)).with_for_update()
    )
    if (
        invitation is None
        or invitation.used_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        return None
    if invitation.organization_id is not None:
        organization = db.get(Organization, invitation.organization_id)
        if organization is None or organization.status != "active":
            return None
    return invitation


# --- Invitations -----------------------------------------------------------------


@router.post("/invitations/preview", response_model=InvitationPreview)
def preview_invitation(
    body: TokenRequest, db: DbSession, now: Now, ip: ClientIp
) -> InvitationPreview:
    _enforce_token_rate(db, ip, now)
    invitation = _open_invitation(db, body.token, now)
    if invitation is None:
        raise _token_failure(db, ip, now, INVITATION_INVALID)
    organization = (
        db.get(Organization, invitation.organization_id) if invitation.organization_id else None
    )
    return InvitationPreview(
        email=invitation.email,
        organization_name=organization.name if organization else None,
        role=invitation.role,
    )


@router.post("/invitations/accept", response_model=LoginResponse)
def accept_invitation(
    body: AcceptInvitationRequest,
    response: Response,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    checker: BreachedChecker,
    ip: ClientIp,
    user_agent: UserAgent,
) -> LoginResponse:
    _enforce_token_rate(db, ip, now)
    invitation = _open_invitation(db, body.token, now)
    if invitation is None:
        raise _token_failure(db, ip, now, INVITATION_INVALID)
    if db.scalar(select(User.id).where(User.email == invitation.email)) is not None:
        # Registered elsewhere since the invitation was sent.
        invitation.revoked_at = now
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVITATION_INVALID)
    try:
        password = validate_new_password(body.password, invitation.email, checker)
    except PasswordPolicyError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    user = User(
        organization_id=invitation.organization_id,
        email=invitation.email,
        role=invitation.role,
        status="active",
        mfa_required=invitation.role in MFA_REQUIRED_ROLES,
    )
    db.add(user)
    db.flush()
    db.add(PasswordCredential(user_id=user.id, hash=hash_password(password)))
    invitation.used_at = now
    audit.record(
        db,
        "auth.invitation_accepted",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        target_type="invitation",
        target_id=invitation.id,
        ip=ip,
        user_agent=user_agent,
    )

    if user.mfa_required:
        raw, digest = new_token()
        db.add(
            LoginChallenge(
                user_id=user.id,
                token_hash=digest,
                expires_at=now + timedelta(seconds=settings.login_challenge_ttl_seconds),
            )
        )
        response.set_cookie(
            MFA_COOKIE,
            raw,
            max_age=settings.login_challenge_ttl_seconds,
            samesite="strict",
            **_cookie_options(settings),
        )
        return LoginResponse(mfa_enrollment_required=True)

    _start_session(
        db,
        response,
        user,
        now=now,
        settings=settings,
        ip=ip,
        user_agent=user_agent,
        method="invitation",
    )
    return LoginResponse()


# --- Password reset ----------------------------------------------------------------


def _issue_password_reset(
    session_factory: sessionmaker[Session],
    queue: EmailQueue,
    settings: Settings,
    email: str,
    now: datetime,
) -> None:
    """Runs after the response, so account existence never changes response timing."""
    with session_factory.begin() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None or user.status != "active":
            return
        if db.get(PasswordCredential, user.id) is None:
            return
        db.execute(
            update(PasswordReset)
            .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
            .values(used_at=now)
        )
        raw, digest = new_token()
        db.add(PasswordReset(user_id=user.id, token_hash=digest, expires_at=now + RESET_TTL))
        audit.record(
            db,
            "auth.password_reset_requested",
            organization_id=user.organization_id,
            actor_user_id=user.id,
        )
    queue.enqueue(
        EmailMessage(
            to=email,
            template="password_reset",
            data={"link": link(settings, "/reset-password", raw)},
        )
    )


@router.post("/password-reset", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    body: ResetRequest,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    queue: EmailOutbox,
    session_factory: SessionFactory,
    ip: ClientIp,
) -> Accepted:
    email = normalize_email(body.email)
    allowed = throttle.consume(db, f"reset-email:{email}", throttle.RESET_EMAIL_POLICY, now)
    if ip is not None:
        allowed = throttle.consume(db, f"reset-ip:{ip}", throttle.RESET_IP_POLICY, now) and allowed
    if allowed:
        after_commit(db, _issue_password_reset, session_factory, queue, settings, email, now)
    # Same answer whether or not the account exists or the request was rate-limited.
    return Accepted(message=RESET_ACCEPTED)


@router.post("/password-reset/complete", status_code=status.HTTP_204_NO_CONTENT)
def complete_password_reset(
    body: ResetCompleteRequest,
    db: DbSession,
    now: Now,
    checker: BreachedChecker,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    _enforce_token_rate(db, ip, now)
    reset = db.scalar(
        select(PasswordReset)
        .where(PasswordReset.token_hash == hash_token(body.token))
        .with_for_update()
    )
    user = db.get(User, reset.user_id) if reset is not None else None
    if (
        reset is None
        or reset.used_at is not None
        or reset.expires_at <= now
        or user is None
        or user.status != "active"
    ):
        raise _token_failure(db, ip, now, LINK_INVALID)
    try:
        password = validate_new_password(body.new_password, user.email, checker)
    except PasswordPolicyError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    credential = db.get(PasswordCredential, user.id)
    if credential is None:
        db.add(PasswordCredential(user_id=user.id, hash=hash_password(password)))
    else:
        credential.hash = hash_password(password)
    db.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    revoke_all_for_user(db, user.id, now)
    throttle.clear(db, throttle.account_key(user.email))
    audit.record(
        db,
        "auth.password_reset_completed",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    after_commit(db, queue.enqueue, EmailMessage(to=user.email, template="password_changed"))


# --- Email change -------------------------------------------------------------------


@router.post("/email-change", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED)
def request_email_change(
    body: EmailChangeRequest,
    active: RecentlyAuthenticated,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> Accepted:
    new_email = normalize_email(body.new_email)
    if "@" not in new_email or new_email == active.user.email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "새 이메일을 확인하세요.")
    db.execute(
        update(EmailChange)
        .where(EmailChange.user_id == active.user.id, EmailChange.used_at.is_(None))
        .values(used_at=now)
    )
    audit.record(
        db,
        "auth.email_change_requested",
        organization_id=active.user.organization_id,
        actor_user_id=active.user.id,
        ip=ip,
        user_agent=user_agent,
    )
    # A taken address gets no email, but the response is the same.
    if db.scalar(select(User.id).where(User.email == new_email)) is None:
        raw, digest = new_token()
        db.add(
            EmailChange(
                user_id=active.user.id,
                new_email=new_email,
                token_hash=digest,
                expires_at=now + EMAIL_CHANGE_TTL,
            )
        )
        after_commit(
            db,
            queue.enqueue,
            EmailMessage(
                to=new_email,
                template="email_change_confirm",
                data={"link": link(settings, "/confirm-email", raw)},
            ),
        )
    return Accepted(message="확인 메일을 새 주소로 보냈습니다.")


@router.post("/email-change/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_email_change(
    body: TokenRequest,
    db: DbSession,
    now: Now,
    queue: EmailOutbox,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    _enforce_token_rate(db, ip, now)
    change = db.scalar(
        select(EmailChange)
        .where(EmailChange.token_hash == hash_token(body.token))
        .with_for_update()
    )
    user = db.get(User, change.user_id) if change is not None else None
    if (
        change is None
        or change.used_at is not None
        or change.expires_at <= now
        or user is None
        or user.status != "active"
    ):
        raise _token_failure(db, ip, now, LINK_INVALID)
    if db.scalar(select(User.id).where(User.email == change.new_email)) is not None:
        change.used_at = now
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, LINK_INVALID)

    old_email = user.email
    user.email = change.new_email
    change.used_at = now
    db.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    audit.record(
        db,
        "auth.email_changed",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    after_commit(
        db,
        queue.enqueue,
        EmailMessage(
            to=old_email,
            template="email_changed_notice",
            data={"new_email_masked": mask_email(change.new_email)},
        ),
    )
