"""Login throttling (docs/Web_Auth_Spec.md §8), stored in login_throttle.

Keys are "account:<normalized email>" (whether or not the account exists) and
"ip:<address>". Locks always expire; there is no permanent lockout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pipe1_server.models.auth import LoginThrottle


@dataclass(frozen=True)
class ThrottlePolicy:
    window: timedelta
    free_failures: int
    lock_after: int
    lock_duration: timedelta
    # Doubling delay applied from the (free_failures + 1)th failure until lock_after.
    base_delay: timedelta

    def lock_for(self, failure_count: int) -> timedelta | None:
        if failure_count >= self.lock_after:
            return self.lock_duration
        if failure_count > self.free_failures:
            return self.base_delay * 2 ** (failure_count - self.free_failures - 1)
        return None


ACCOUNT_POLICY = ThrottlePolicy(
    window=timedelta(minutes=15),
    free_failures=4,
    lock_after=10,
    lock_duration=timedelta(minutes=15),
    base_delay=timedelta(seconds=2),
)
IP_POLICY = ThrottlePolicy(
    window=timedelta(minutes=15),
    free_failures=50,
    lock_after=100,
    lock_duration=timedelta(minutes=15),
    base_delay=timedelta(seconds=1),
)


def account_key(normalized_email: str) -> str:
    return f"account:{normalized_email}"


def ip_key(address: str) -> str:
    return f"ip:{address}"


def locked_until(session: Session, key: str, now: datetime) -> datetime | None:
    until = session.scalar(select(LoginThrottle.locked_until).where(LoginThrottle.key == key))
    return until if until is not None and until > now else None


def record_failure(session: Session, key: str, policy: ThrottlePolicy, now: datetime) -> int:
    """Count one failure; returns the failure count within the current window."""
    window_expired = LoginThrottle.window_started_at < now - policy.window
    statement = (
        insert(LoginThrottle)
        .values(key=key, failure_count=1, window_started_at=now)
        .on_conflict_do_update(
            index_elements=[LoginThrottle.key],
            set_={
                "failure_count": case((window_expired, 1), else_=LoginThrottle.failure_count + 1),
                "window_started_at": case(
                    (window_expired, now), else_=LoginThrottle.window_started_at
                ),
            },
        )
        .returning(LoginThrottle.failure_count)
    )
    failure_count = session.execute(statement).scalar_one()
    lock = policy.lock_for(failure_count)
    if lock is not None:
        session.execute(
            update(LoginThrottle).where(LoginThrottle.key == key).values(locked_until=now + lock)
        )
    return failure_count


def clear(session: Session, key: str) -> None:
    session.execute(delete(LoginThrottle).where(LoginThrottle.key == key))


def _request_policy(limit: int, window: timedelta) -> ThrottlePolicy:
    """Count every request; the limit-th request within the window locks for the window."""
    return ThrottlePolicy(
        window=window,
        free_failures=limit,
        lock_after=limit,
        lock_duration=window,
        base_delay=timedelta(0),
    )


# Password-reset requests per target email and per IP.
RESET_EMAIL_POLICY = _request_policy(5, timedelta(hours=1))
RESET_IP_POLICY = _request_policy(20, timedelta(hours=1))
# Failed token submissions (invitation accept, reset complete, email confirm) per IP.
TOKEN_IP_POLICY = ThrottlePolicy(
    window=timedelta(minutes=15),
    free_failures=20,
    lock_after=30,
    lock_duration=timedelta(minutes=15),
    base_delay=timedelta(seconds=1),
)
# Invitations created per admin.
INVITE_ADMIN_POLICY = _request_policy(50, timedelta(hours=1))


def consume(session: Session, key: str, policy: ThrottlePolicy, now: datetime) -> bool:
    """Rate-limit a request: False if the key is locked, otherwise count it and return True."""
    if locked_until(session, key, now) is not None:
        return False
    record_failure(session, key, policy, now)
    return True
