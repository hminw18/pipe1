"""Request-scoped dependencies shared by all routes (docs/Web_Auth_Spec.md §10)."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from pipe1_server.db import get_db, get_session_factory, set_access_context
from pipe1_server.email.delivery import EmailQueue, build_queue
from pipe1_server.jobs import (
    BackgroundJobQueue,
    CloudTasksJobQueue,
    InlineJobQueue,
    JobQueue,
    JobRunner,
    LocalMediaClient,
    MediaClient,
    WorkerMediaClient,
)
from pipe1_server.security.csrf import csrf_token_valid
from pipe1_server.security.passwords import (
    BreachedPasswordChecker,
    HibpChecker,
    OfflineListChecker,
)
from pipe1_server.security.sessions import (
    ActiveSession,
    is_recently_authenticated,
    load_active_session,
    touch,
)
from pipe1_server.security.totp import KmsCipher, LocalAesGcmCipher, SecretCipher
from pipe1_server.settings import Settings, get_settings
from pipe1_server.storage import ObjectStore, build_store

SESSION_COOKIE = "__Host-pipe1_session"
MFA_COOKIE = "__Host-pipe1_mfa"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_now() -> datetime:
    return datetime.now(UTC)


def client_ip(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> str | None:
    """Client address, taken from X-Forwarded-For only as far as trusted proxies vouch for it."""
    candidate: str | None = None
    if settings.trusted_proxy_hops > 0:
        hops = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",")]
        hops = [hop for hop in hops if hop]
        if len(hops) >= settings.trusted_proxy_hops:
            candidate = hops[-settings.trusted_proxy_hops]
    elif request.client is not None:
        candidate = request.client.host
    if candidate is None:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


@lru_cache
def _breached_checker(source: str, file: str | None) -> BreachedPasswordChecker:
    if source == "hibp":
        return HibpChecker()
    if file:
        return OfflineListChecker(Path(file))

    class _NoList:
        def is_breached(self, password: str) -> bool:
            return False

    return _NoList()  # dev only; settings require a list outside dev


def get_breached_checker(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BreachedPasswordChecker:
    return _breached_checker(settings.breached_password_source, settings.breached_password_file)


@lru_cache
def _cipher(kms_key: str | None, local_key: str | None) -> SecretCipher:
    if kms_key:
        return KmsCipher(kms_key)
    if local_key:
        return LocalAesGcmCipher(local_key)
    raise RuntimeError("Set PIPE1_TOTP_KMS_KEY or, for development, PIPE1_TOTP_LOCAL_KEY")


def get_totp_cipher(settings: Annotated[Settings, Depends(get_settings)]) -> SecretCipher:
    local = settings.totp_local_key.get_secret_value() if settings.totp_local_key else None
    return _cipher(settings.totp_kms_key, local)


DbSession = Annotated[Session, Depends(get_db)]
Now = Annotated[datetime, Depends(get_now)]
AppSettings = Annotated[Settings, Depends(get_settings)]
ClientIp = Annotated[str | None, Depends(client_ip)]
UserAgent = Annotated[str | None, Depends(user_agent)]


def current_session(
    request: Request, db: DbSession, now: Now, settings: AppSettings
) -> ActiveSession:
    """Authenticated session for this request, with CSRF verified and RLS context applied."""
    raw = request.cookies.get(SESSION_COOKIE)
    active = load_active_session(db, raw, now) if raw else None
    if active is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")
    if request.method in UNSAFE_METHODS and not csrf_token_valid(
        settings.csrf_key.get_secret_value(), active.session.id, request.headers.get(CSRF_HEADER)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    touch(active.session, now, settings)
    set_access_context(
        db,
        organization_id=active.user.organization_id,
        user_id=active.user.id,
        role=active.user.role,
    )
    return active


CurrentSession = Annotated[ActiveSession, Depends(current_session)]


def require_recent_authentication(
    active: CurrentSession, now: Now, settings: AppSettings
) -> ActiveSession:
    if not is_recently_authenticated(active.session, now, settings):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "reauthentication_required", "message": "비밀번호를 다시 입력하세요."},
        )
    return active


RecentlyAuthenticated = Annotated[ActiveSession, Depends(require_recent_authentication)]


@lru_cache
def _email_queue(
    queue: str,
    sender: str,
    from_address: str,
    tasks_queue: str | None,
    worker_url: str | None,
    tasks_account: str | None,
    api_key: str | None,
    mailer_url: str | None = None,
) -> EmailQueue:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        email_queue=queue,
        email_sender=sender,
        email_from=from_address,
        cloud_tasks_queue=tasks_queue,
        worker_url=worker_url,
        tasks_service_account=tasks_account,
        resend_api_key=api_key,
        mailer_url=mailer_url,
    )
    return build_queue(settings)


def get_email_queue(settings: AppSettings) -> EmailQueue:
    return _email_queue(
        settings.email_queue,
        settings.email_sender,
        settings.email_from,
        settings.cloud_tasks_queue,
        settings.worker_url,
        settings.tasks_service_account,
        settings.resend_api_key.get_secret_value() if settings.resend_api_key else None,
        settings.mailer_url,
    )


EmailOutbox = Annotated[EmailQueue, Depends(get_email_queue)]
SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def require_role(*roles: str) -> Callable[[ActiveSession], ActiveSession]:
    def guard(active: CurrentSession) -> ActiveSession:
        if active.user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "권한이 없습니다.")
        return active

    return guard


OrgAdmin = Annotated[ActiveSession, Depends(require_role("org_admin"))]
PlatformAdmin = Annotated[ActiveSession, Depends(require_role("platform_admin"))]


def get_object_store(settings: AppSettings) -> ObjectStore:
    return build_store(settings)


Store = Annotated[ObjectStore, Depends(get_object_store)]


def get_job_queue(settings: AppSettings, session_factory: SessionFactory, store: Store) -> JobQueue:
    if settings.job_queue == "cloud_tasks":
        assert settings.cloud_tasks_queue and settings.worker_url and settings.tasks_service_account
        return _cloud_tasks_job_queue(
            settings.cloud_tasks_queue, settings.worker_url, settings.tasks_service_account
        )
    if settings.job_queue == "background":
        return background_job_queue()
    return InlineJobQueue(JobRunner(session_factory, store))


@lru_cache
def background_job_queue() -> BackgroundJobQueue:
    """One per process, so its threads and queue outlive the request that enqueued a job."""
    return BackgroundJobQueue(JobRunner(get_session_factory(), build_store(get_settings())))


@lru_cache
def _cloud_tasks_job_queue(queue: str, worker_url: str, account: str) -> JobQueue:
    return CloudTasksJobQueue(queue, worker_url, account)


def get_media_client(settings: AppSettings, store: Store) -> MediaClient:
    if settings.media_client == "worker":
        assert settings.worker_url
        return WorkerMediaClient(settings.worker_url)
    return LocalMediaClient(store)


Jobs = Annotated[JobQueue, Depends(get_job_queue)]
Media = Annotated[MediaClient, Depends(get_media_client)]
