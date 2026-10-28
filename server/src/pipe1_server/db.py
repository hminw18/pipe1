from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache, partial
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from pipe1_server.settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def set_access_context(
    session: Session,
    *,
    organization_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    role: str | None,
) -> None:
    """Set the transaction-local identity read by the row-level security policies.

    Must be called inside the transaction that runs the request's queries; the
    values reset when the transaction ends, so pooled connections never leak
    one user's identity to the next request.
    """
    session.execute(
        text(
            "SELECT set_config('app.org_id', :org_id, true),"
            " set_config('app.user_id', :user_id, true),"
            " set_config('app.role', :role, true)"
        ),
        {
            "org_id": str(organization_id) if organization_id else "",
            "user_id": str(user_id) if user_id else "",
            "role": role or "",
        },
    )


def get_session_factory() -> sessionmaker[Session]:
    """FastAPI dependency for work that needs its own transaction (e.g. background jobs)."""
    return _session_factory()


AFTER_COMMIT = "pipe1_after_commit"


def after_commit(db: Session, callback: Callable[..., object], *args: object) -> None:
    """Run callback(*args) once the request's transaction has committed.

    Use this, not FastAPI BackgroundTasks, for anything that must see committed data or must
    not happen if the transaction rolls back (enqueueing jobs and emails, deleting files):
    FastAPI runs background tasks before dependency teardown, i.e. before the commit.
    """
    db.info.setdefault(AFTER_COMMIT, []).append(partial(callback, *args))


def get_db(
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Iterator[Session]:
    """FastAPI dependency: one transaction per request, committed when the handler succeeds,
    then the after_commit callbacks run.

    Handlers that must persist state while returning an error (e.g. failed-login
    counters) call ``db.commit()`` before raising.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    else:
        for callback in session.info.pop(AFTER_COMMIT, []):
            try:
                callback()
            except Exception:  # noqa: BLE001 - the request already succeeded
                logger.exception("after-commit callback failed")
    finally:
        session.close()


@contextmanager
def session_scope(
    *,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
) -> Iterator[Session]:
    """Open a transaction with the access context applied; commit on success."""
    with _session_factory().begin() as session:
        set_access_context(session, organization_id=organization_id, user_id=user_id, role=role)
        yield session
