"""Append-only audit trail (docs/Web_Auth_Spec.md §12). Never pass secrets in metadata."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from pipe1_server.models.auth import AuditEvent


def record(
    db: Session,
    action: str,
    *,
    organization_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip=ip,
            user_agent=(user_agent or "")[:512] or None,
            metadata_=metadata or {},
        )
    )
