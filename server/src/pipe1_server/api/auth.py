"""Login, MFA, sessions, re-authentication and password change (docs/Web_Auth_Spec.md §4-§9)."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from pipe1_server.api.deps import (
    MFA_COOKIE,
    SESSION_COOKIE,
    AppSettings,
    ClientIp,
    CurrentSession,
    DbSession,
    EmailOutbox,
    Now,
    RecentlyAuthenticated,
    UserAgent,
    get_breached_checker,
    get_totp_cipher,
)
from pipe1_server.db import after_commit
from pipe1_server.email.delivery import EmailMessage
from pipe1_server.models.auth import (
    AuthSession,
    LoginChallenge,
    MfaTotp,
    Organization,
    PasswordCredential,
    PasswordReset,
    RecoveryCode,
    User,
)
from pipe1_server.security import audit, recovery_codes, throttle, totp
from pipe1_server.security.csrf import csrf_token
from pipe1_server.security.emails import normalize_email
from pipe1_server.security.passwords import (
    BreachedPasswordChecker,
    PasswordPolicyError,
    burn_verification_time,
    hash_password,
    validate_new_password,
    verify_password,
)
from pipe1_server.security.sessions import create_session, revoke, revoke_all_for_user
from pipe1_server.security.tokens import hash_token, new_token
from pipe1_server.security.totp import SecretCipher
from pipe1_server.settings import Settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Only Pipe1 operators must use MFA; everyone else, org admins included, may opt in.
MFA_REQUIRED_ROLES = frozenset({"platform_admin"})
LOGIN_FAILED = "이메일 또는 비밀번호가 올바르지 않습니다."
CODE_FAILED = "인증 코드가 올바르지 않습니다."
CHALLENGE_EXPIRED = "로그인 시간이 만료되었습니다. 다시 로그인하세요."
# Bounds Argon2 work per request; the policy maximum is 128 characters.
MAX_PASSWORD_INPUT = 1024

Cipher = Annotated[SecretCipher, Depends(get_totp_cipher)]
BreachedChecker = Annotated[BreachedPasswordChecker, Depends(get_breached_checker)]


# --- Schemas -------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=MAX_PASSWORD_INPUT)


class LoginResponse(BaseModel):
    mfa_required: bool = False
    mfa_enrollment_required: bool = False


class CodeRequest(BaseModel):
    code: str = Field(max_length=64)


class EnrollmentStart(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodes(BaseModel):
    recovery_codes: list[str]


class Me(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    organization_id: uuid.UUID | None
    organization_name: str | None
    mfa_enrolled: bool
    csrf_token: str
    authenticated_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime


class SessionExpiry(BaseModel):
    idle_expires_at: datetime
    absolute_expires_at: datetime


class SessionInfo(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


class ReauthenticateRequest(BaseModel):
    password: str = Field(max_length=MAX_PASSWORD_INPUT)
    code: str | None = Field(default=None, max_length=64)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD_INPUT)
    new_password: str = Field(max_length=MAX_PASSWORD_INPUT)


# --- Helpers -------------------------------------------------------------------


def _too_many_attempts(until: datetime, now: datetime) -> HTTPException:
    seconds = max(1, math.ceil((until - now).total_seconds()))
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "시도 횟수가 너무 많습니다. 잠시 후 다시 시도하세요.",
        headers={"Retry-After": str(seconds)},
    )


def _throttle_keys(email: str, ip: str | None) -> list[tuple[str, throttle.ThrottlePolicy]]:
    keys = [(throttle.account_key(email), throttle.ACCOUNT_POLICY)]
    if ip:
        keys.append((throttle.ip_key(ip), throttle.IP_POLICY))
    return keys


def _enforce_throttle(db: Session, email: str, ip: str | None, now: datetime) -> None:
    for key, _ in _throttle_keys(email, ip):
        until = throttle.locked_until(db, key, now)
        if until is not None:
            raise _too_many_attempts(until, now)


def _record_failure(db: Session, email: str, ip: str | None, now: datetime) -> None:
    for key, policy in _throttle_keys(email, ip):
        throttle.record_failure(db, key, policy, now)


def _mfa_enrolled(db: Session, user_id: uuid.UUID) -> MfaTotp | None:
    record = db.get(MfaTotp, user_id)
    return record if record is not None and record.confirmed_at is not None else None


def _cookie_options(settings: Settings) -> dict[str, object]:
    return {"secure": True, "httponly": True, "path": "/"}


def _start_session(
    db: Session,
    response: Response,
    user: User,
    *,
    now: datetime,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
    method: str,
) -> None:
    raw, record = create_session(
        db, user_id=user.id, now=now, settings=settings, ip=ip, user_agent=user_agent
    )
    user.last_login_at = now
    throttle.clear(db, throttle.account_key(user.email))
    audit.record(
        db,
        "auth.login_succeeded",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        target_type="session",
        target_id=record.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"method": method},
    )
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=settings.session_absolute_timeout_seconds,
        samesite="lax",
        **_cookie_options(settings),
    )
    response.delete_cookie(MFA_COOKIE, samesite="strict", **_cookie_options(settings))


def _load_challenge(
    request: Request, db: Session, now: datetime, settings: Settings
) -> tuple[LoginChallenge, User]:
    raw = request.cookies.get(MFA_COOKIE)
    challenge = (
        db.scalar(
            select(LoginChallenge)
            .where(LoginChallenge.token_hash == hash_token(raw))
            .with_for_update()
        )
        if raw
        else None
    )
    if (
        challenge is None
        or challenge.used_at is not None
        or challenge.expires_at <= now
        or challenge.attempt_count >= settings.login_challenge_max_attempts
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, CHALLENGE_EXPIRED)
    user = db.get(User, challenge.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, CHALLENGE_EXPIRED)
    return challenge, user


def _fail_challenge(
    db: Session,
    challenge: LoginChallenge,
    user: User,
    *,
    now: datetime,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
) -> HTTPException:
    challenge.attempt_count += 1
    if challenge.attempt_count >= settings.login_challenge_max_attempts:
        challenge.used_at = now
    throttle.record_failure(db, throttle.account_key(user.email), throttle.ACCOUNT_POLICY, now)
    audit.record(
        db,
        "auth.mfa_failed",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"attempt": challenge.attempt_count},
    )
    db.commit()
    return HTTPException(status.HTTP_401_UNAUTHORIZED, CODE_FAILED)


def _verify_second_factor(
    db: Session, user: User, code: str, cipher: SecretCipher, now: datetime
) -> str | None:
    """Check a TOTP or recovery code. Returns "totp"/"recovery_code" on success."""
    record = db.scalar(select(MfaTotp).where(MfaTotp.user_id == user.id).with_for_update())
    if record is None or record.confirmed_at is None:
        return None
    secret = cipher.decrypt(record.secret_ciphertext).decode("ascii")
    step = totp.verify(secret, code, record.last_used_step, now.timestamp())
    if step is not None:
        record.last_used_step = step
        return "totp"
    if recovery_codes.looks_like_recovery_code(code):
        stored = db.scalar(
            select(RecoveryCode)
            .where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.code_hash == recovery_codes.code_hash(code),
                RecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
        if stored is not None:
            stored.used_at = now
            return "recovery_code"
    return None


def _issue_recovery_codes(db: Session, user_id: uuid.UUID) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    codes = recovery_codes.generate()
    db.add_all(
        RecoveryCode(user_id=user_id, code_hash=recovery_codes.code_hash(code)) for code in codes
    )
    return codes


# --- Login ---------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    response: Response,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    ip: ClientIp,
    user_agent: UserAgent,
) -> LoginResponse:
    email = normalize_email(body.email)
    _enforce_throttle(db, email, ip, now)

    user = db.scalar(select(User).where(User.email == email))
    credential = db.get(PasswordCredential, user.id) if user is not None else None
    organization = (
        db.get(Organization, user.organization_id)
        if user is not None and user.organization_id is not None
        else None
    )
    valid, upgraded_hash = False, None
    if credential is not None:
        valid, upgraded_hash = verify_password(body.password, credential.hash)
    else:
        burn_verification_time(body.password)

    if (
        user is None
        or not valid
        or user.status != "active"
        or (organization is not None and organization.status != "active")
    ):
        _record_failure(db, email, ip, now)
        audit.record(
            db,
            "auth.login_failed",
            organization_id=user.organization_id if user else None,
            actor_user_id=user.id if user else None,
            ip=ip,
            user_agent=user_agent,
            metadata={"known_account": user is not None},
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGIN_FAILED)

    # The failure counter is cleared only when a full login succeeds (_start_session);
    # clearing it here would let a password holder reset MFA guessing limits at will.
    if upgraded_hash is not None and credential is not None:
        credential.hash = upgraded_hash

    enrolled = _mfa_enrolled(db, user.id) is not None
    if enrolled or user.mfa_required or user.role in MFA_REQUIRED_ROLES:
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
        return LoginResponse(mfa_required=enrolled, mfa_enrollment_required=not enrolled)

    _start_session(
        db,
        response,
        user,
        now=now,
        settings=settings,
        ip=ip,
        user_agent=user_agent,
        method="password",
    )
    return LoginResponse()


@router.post("/mfa", response_model=LoginResponse)
def verify_mfa(
    body: CodeRequest,
    request: Request,
    response: Response,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    cipher: Cipher,
    ip: ClientIp,
    user_agent: UserAgent,
) -> LoginResponse:
    challenge, user = _load_challenge(request, db, now, settings)
    until = throttle.locked_until(db, throttle.account_key(user.email), now)
    if until is not None:
        raise _too_many_attempts(until, now)
    method = _verify_second_factor(db, user, body.code, cipher, now)
    if method is None:
        raise _fail_challenge(
            db, challenge, user, now=now, settings=settings, ip=ip, user_agent=user_agent
        )
    challenge.used_at = now
    _start_session(
        db,
        response,
        user,
        now=now,
        settings=settings,
        ip=ip,
        user_agent=user_agent,
        method=f"password+{method}",
    )
    return LoginResponse()


@router.post("/mfa/enrollment", response_model=EnrollmentStart)
def start_mfa_enrollment(
    request: Request, db: DbSession, now: Now, settings: AppSettings, cipher: Cipher
) -> EnrollmentStart:
    """First-login TOTP enrollment for users who must use MFA but have none yet."""
    _, user = _load_challenge(request, db, now, settings)
    existing = db.get(MfaTotp, user.id)
    if existing is not None and existing.confirmed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 등록되어 있습니다.")
    secret = totp.new_secret()
    ciphertext = cipher.encrypt(secret.encode("ascii"))
    if existing is None:
        db.add(MfaTotp(user_id=user.id, secret_ciphertext=ciphertext))
    else:
        existing.secret_ciphertext = ciphertext
        existing.last_used_step = None
    return EnrollmentStart(
        secret=secret,
        otpauth_uri=totp.provisioning_uri(secret, user.email, settings.totp_issuer),
    )


@router.post("/mfa/enrollment/confirm", response_model=RecoveryCodes)
def confirm_mfa_enrollment(
    body: CodeRequest,
    request: Request,
    response: Response,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    cipher: Cipher,
    ip: ClientIp,
    user_agent: UserAgent,
) -> RecoveryCodes:
    challenge, user = _load_challenge(request, db, now, settings)
    record = db.scalar(select(MfaTotp).where(MfaTotp.user_id == user.id).with_for_update())
    if record is None or record.confirmed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "등록을 다시 시작하세요.")
    secret = cipher.decrypt(record.secret_ciphertext).decode("ascii")
    step = totp.verify(secret, body.code, None, now.timestamp())
    if step is None:
        raise _fail_challenge(
            db, challenge, user, now=now, settings=settings, ip=ip, user_agent=user_agent
        )
    record.confirmed_at = now
    record.last_used_step = step
    codes = _issue_recovery_codes(db, user.id)
    challenge.used_at = now
    audit.record(
        db,
        "auth.mfa_enrolled",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    _start_session(
        db,
        response,
        user,
        now=now,
        settings=settings,
        ip=ip,
        user_agent=user_agent,
        method="password+totp_enrollment",
    )
    return RecoveryCodes(recovery_codes=codes)


# --- Session ---------------------------------------------------------------------


@router.get("/me", response_model=Me)
def me(active: CurrentSession, db: DbSession, settings: AppSettings) -> Me:
    return Me(
        id=active.user.id,
        email=active.user.email,
        role=active.user.role,
        organization_id=active.user.organization_id,
        organization_name=active.organization.name if active.organization else None,
        mfa_enrolled=_mfa_enrolled(db, active.user.id) is not None,
        csrf_token=csrf_token(settings.csrf_key.get_secret_value(), active.session.id),
        authenticated_at=active.session.authenticated_at,
        idle_expires_at=active.session.idle_expires_at,
        absolute_expires_at=active.session.absolute_expires_at,
    )


@router.post("/heartbeat", response_model=SessionExpiry)
def heartbeat(active: CurrentSession) -> SessionExpiry:
    # current_session already moved the idle timeout forward.
    return SessionExpiry(
        idle_expires_at=active.session.idle_expires_at,
        absolute_expires_at=active.session.absolute_expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    active: CurrentSession,
    response: Response,
    db: DbSession,
    now: Now,
    settings: AppSettings,
    ip: ClientIp,
    user_agent: UserAgent,
) -> Response:
    revoke(active.session, now)
    audit.record(
        db,
        "auth.logout",
        organization_id=active.user.organization_id,
        actor_user_id=active.user.id,
        target_type="session",
        target_id=active.session.id,
        ip=ip,
        user_agent=user_agent,
    )
    response.delete_cookie(SESSION_COOKIE, samesite="lax", **_cookie_options(settings))
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/sessions", response_model=list[SessionInfo])
def list_sessions(active: CurrentSession, db: DbSession, now: Now) -> list[SessionInfo]:
    records = db.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == active.user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.idle_expires_at > now,
            AuthSession.absolute_expires_at > now,
        )
        .order_by(AuthSession.last_seen_at.desc())
    )
    return [
        SessionInfo(
            id=record.id,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            ip=str(record.ip) if record.ip is not None else None,
            user_agent=record.user_agent,
            current=record.id == active.session.id,
        )
        for record in records
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    now: Now,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    record = db.get(AuthSession, session_id)
    if record is None or record.user_id != active.user.id or record.revoked_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    revoke(record, now)
    audit.record(
        db,
        "auth.session_revoked",
        organization_id=active.user.organization_id,
        actor_user_id=active.user.id,
        target_type="session",
        target_id=record.id,
        ip=ip,
        user_agent=user_agent,
    )


@router.post("/sessions/revoke-others", status_code=status.HTTP_204_NO_CONTENT)
def revoke_other_sessions(
    active: CurrentSession, db: DbSession, now: Now, ip: ClientIp, user_agent: UserAgent
) -> None:
    revoke_all_for_user(db, active.user.id, now, except_session_id=active.session.id)
    audit.record(
        db,
        "auth.sessions_revoked",
        organization_id=active.user.organization_id,
        actor_user_id=active.user.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"scope": "others"},
    )


# --- Re-authentication and password change ------------------------------------------


def _check_password_for_sensitive_action(
    db: Session, user: User, password: str, *, now: datetime, ip: str | None, user_agent: str | None
) -> None:
    _enforce_throttle(db, user.email, ip, now)
    credential = db.get(PasswordCredential, user.id)
    valid = credential is not None and verify_password(password, credential.hash)[0]
    if not valid:
        _record_failure(db, user.email, ip, now)
        audit.record(
            db,
            "auth.reauthentication_failed",
            organization_id=user.organization_id,
            actor_user_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "비밀번호가 올바르지 않습니다.")


@router.post("/reauthenticate", response_model=SessionExpiry)
def reauthenticate(
    body: ReauthenticateRequest,
    active: CurrentSession,
    db: DbSession,
    now: Now,
    cipher: Cipher,
    ip: ClientIp,
    user_agent: UserAgent,
) -> SessionExpiry:
    user = active.user
    _check_password_for_sensitive_action(
        db, user, body.password, now=now, ip=ip, user_agent=user_agent
    )
    if _mfa_enrolled(db, user.id) is not None:
        if not body.code or _verify_second_factor(db, user, body.code, cipher, now) is None:
            _record_failure(db, user.email, ip, now)
            db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, CODE_FAILED)
    throttle.clear(db, throttle.account_key(user.email))
    active.session.authenticated_at = now
    audit.record(
        db,
        "auth.reauthenticated",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    return SessionExpiry(
        idle_expires_at=active.session.idle_expires_at,
        absolute_expires_at=active.session.absolute_expires_at,
    )


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeRequest,
    active: CurrentSession,
    queue: EmailOutbox,
    db: DbSession,
    now: Now,
    checker: BreachedChecker,
    ip: ClientIp,
    user_agent: UserAgent,
) -> None:
    user = active.user
    _check_password_for_sensitive_action(
        db, user, body.current_password, now=now, ip=ip, user_agent=user_agent
    )
    try:
        new_password = validate_new_password(body.new_password, user.email, checker)
    except PasswordPolicyError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    credential = db.get(PasswordCredential, user.id)
    assert credential is not None  # verified above
    credential.hash = hash_password(new_password)
    throttle.clear(db, throttle.account_key(user.email))
    revoke_all_for_user(db, user.id, now, except_session_id=active.session.id)
    db.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    active.session.authenticated_at = now
    audit.record(
        db,
        "auth.password_changed",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    after_commit(db, queue.enqueue, EmailMessage(to=user.email, template="password_changed"))


# --- MFA management for signed-in users (optional for inspectors) ---------------------


@router.post("/mfa/setup", response_model=EnrollmentStart)
def start_mfa_setup(
    active: RecentlyAuthenticated, db: DbSession, settings: AppSettings, cipher: Cipher
) -> EnrollmentStart:
    existing = db.get(MfaTotp, active.user.id)
    if existing is not None and existing.confirmed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 등록되어 있습니다.")
    secret = totp.new_secret()
    ciphertext = cipher.encrypt(secret.encode("ascii"))
    if existing is None:
        db.add(MfaTotp(user_id=active.user.id, secret_ciphertext=ciphertext))
    else:
        existing.secret_ciphertext, existing.last_used_step = ciphertext, None
    return EnrollmentStart(
        secret=secret,
        otpauth_uri=totp.provisioning_uri(secret, active.user.email, settings.totp_issuer),
    )


@router.post("/mfa/setup/confirm", response_model=RecoveryCodes)
def confirm_mfa_setup(
    body: CodeRequest,
    active: RecentlyAuthenticated,
    db: DbSession,
    now: Now,
    cipher: Cipher,
    ip: ClientIp,
    user_agent: UserAgent,
) -> RecoveryCodes:
    record = db.scalar(select(MfaTotp).where(MfaTotp.user_id == active.user.id).with_for_update())
    if record is None or record.confirmed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "등록을 다시 시작하세요.")
    secret = cipher.decrypt(record.secret_ciphertext).decode("ascii")
    step = totp.verify(secret, body.code, None, now.timestamp())
    if step is None:
        _record_failure(db, active.user.email, ip, now)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, CODE_FAILED)
    record.confirmed_at, record.last_used_step = now, step
    codes = _issue_recovery_codes(db, active.user.id)
    audit.record(
        db,
        "auth.mfa_enrolled",
        organization_id=active.user.organization_id,
        actor_user_id=active.user.id,
        ip=ip,
        user_agent=user_agent,
    )
    return RecoveryCodes(recovery_codes=codes)


@router.post("/mfa/recovery-codes", response_model=RecoveryCodes)
def regenerate_recovery_codes(
    active: RecentlyAuthenticated, db: DbSession, ip: ClientIp, user_agent: UserAgent
) -> RecoveryCodes:
    if _mfa_enrolled(db, active.user.id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "2단계 인증이 등록되어 있지 않습니다.")
    codes = _issue_recovery_codes(db, active.user.id)
    audit.record(
        db,
        "auth.recovery_codes_regenerated",
        organization_id=active.user.organization_id,
        actor_user_id=active.user.id,
        ip=ip,
        user_agent=user_agent,
    )
    return RecoveryCodes(recovery_codes=codes)


@router.delete("/mfa", status_code=status.HTTP_204_NO_CONTENT)
def disable_mfa(
    active: RecentlyAuthenticated, db: DbSession, ip: ClientIp, user_agent: UserAgent
) -> None:
    user = active.user
    if user.role in MFA_REQUIRED_ROLES or user.mfa_required:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "이 계정은 2단계 인증을 해제할 수 없습니다.")
    db.execute(delete(MfaTotp).where(MfaTotp.user_id == user.id))
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    audit.record(
        db,
        "auth.mfa_disabled",
        organization_id=user.organization_id,
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
