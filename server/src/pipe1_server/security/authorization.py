"""The project permission matrix (docs/Web_Auth_Spec.md §2), in one place.

Row-level security enforces the same rules in the database; this module decides the HTTP
outcome. Anything the user may not view is reported as 404, never 403.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from pipe1_server.models.auth import User
from pipe1_server.models.inspection import InspectionProject
from pipe1_server.security import audit

Action = Literal["view", "edit", "manage", "delete", "transfer"]


def can(user: User, project: InspectionProject, action: Action) -> bool:
    if user.organization_id is None or project.organization_id != user.organization_id:
        return False
    is_owner = project.owner_user_id == user.id
    is_admin = user.role == "org_admin"
    if action in ("view", "edit"):
        return project.visibility == "org" or is_owner or is_admin
    if action in ("manage", "delete"):
        return is_owner or is_admin
    if action == "transfer":
        return is_admin
    return False


def visible_projects(user: User) -> Select[tuple[InspectionProject]]:
    """Scoped query for list endpoints; always applies the organization and visibility rule."""
    query = select(InspectionProject).where(
        InspectionProject.organization_id == user.organization_id
    )
    if user.role != "org_admin":
        query = query.where(
            or_(InspectionProject.visibility == "org", InspectionProject.owner_user_id == user.id)
        )
    return query


def authorize(
    db: Session,
    user: User,
    project_id: uuid.UUID,
    action: Action,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> InspectionProject:
    """Load a project for an action, or raise 404 (not viewable) / 403 (viewable, not allowed).

    Org-admin access to a project they do not own is audited (docs/Web_Auth_Spec.md §2).
    """
    project = db.get(InspectionProject, project_id)
    if project is None or not can(user, project, "view"):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not can(user, project, action):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "권한이 없습니다.")
    if user.role == "org_admin" and project.owner_user_id != user.id:
        audit.record(
            db,
            "project.admin_access",
            organization_id=project.organization_id,
            actor_user_id=user.id,
            target_type="project",
            target_id=project.id,
            ip=ip,
            user_agent=user_agent,
            metadata={"action": action},
        )
    return project
