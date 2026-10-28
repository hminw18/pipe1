"""Projects and businesses (docs/Product_Requirements.md §4.1-§4.2, §6.1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pipe1_server.api.deps import (
    ClientIp,
    CurrentSession,
    DbSession,
    RecentlyAuthenticated,
    Store,
    UserAgent,
)
from pipe1_server.db import after_commit
from pipe1_server.models.auth import User
from pipe1_server.models.inspection import Business, InspectionProject, Report, ReportGroup
from pipe1_server.security import audit
from pipe1_server.security.authorization import authorize, can, visible_projects
from pipe1_server.services import reports as report_service
from pipe1_server.storage import project_prefix, report_group_prefix, report_prefix

router = APIRouter(prefix="/api", tags=["projects"])

Visibility = Literal["private", "org"]


class ProjectCreate(BaseModel):
    project_name: str = Field(min_length=1, max_length=200)
    visibility: Visibility


class ProjectUpdate(BaseModel):
    project_name: str | None = Field(default=None, min_length=1, max_length=200)
    visibility: Visibility | None = None


class ProjectTransfer(BaseModel):
    owner_user_id: uuid.UUID


class ProjectOut(BaseModel):
    id: uuid.UUID
    project_name: str
    visibility: str
    owner_user_id: uuid.UUID
    owner_email: str | None
    is_owner: bool
    can_manage: bool
    business_count: int
    created_at: datetime
    updated_at: datetime


class BusinessFields(BaseModel):
    business_code: str | None = Field(default=None, min_length=1, max_length=100)
    business_name: str | None = Field(default=None, min_length=1, max_length=200)
    client: str | None = Field(default=None, max_length=200)
    business_start_date: str | None = Field(default=None, max_length=50)
    business_end_date: str | None = Field(default=None, max_length=50)


class BusinessCreate(BusinessFields):
    business_code: str = Field(min_length=1, max_length=100)
    business_name: str = Field(min_length=1, max_length=200)


class BusinessOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    business_code: str
    business_name: str
    client: str | None
    business_start_date: str | None
    business_end_date: str | None
    report_count: int
    created_at: datetime
    updated_at: datetime


def _project_out(db: Session, user: User, project: InspectionProject) -> ProjectOut:
    owner = db.get(User, project.owner_user_id)
    count = db.scalar(
        select(func.count()).select_from(Business).where(Business.project_id == project.id)
    )
    return ProjectOut(
        id=project.id,
        project_name=project.project_name,
        visibility=project.visibility,
        owner_user_id=project.owner_user_id,
        owner_email=owner.email if owner else None,
        is_owner=project.owner_user_id == user.id,
        can_manage=can(user, project, "manage"),
        business_count=count or 0,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _business_out(db: Session, business: Business) -> BusinessOut:
    count = db.scalar(
        select(func.count()).select_from(ReportGroup).where(ReportGroup.business_id == business.id)
    )
    return BusinessOut(
        id=business.id,
        project_id=business.project_id,
        business_code=business.business_code,
        business_name=business.business_name,
        client=business.client,
        business_start_date=business.business_start_date,
        business_end_date=business.business_end_date,
        report_count=count or 0,
        created_at=business.created_at,
        updated_at=business.updated_at,
    )


def _conflict(error: IntegrityError, message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


def load_business(
    db: Session, user: User, business_id: uuid.UUID, action, ip=None, user_agent=None
) -> Business:
    business = db.get(Business, business_id)
    if business is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    authorize(db, user, business.project_id, action, ip=ip, user_agent=user_agent)
    return business


# --- Projects -----------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(active: CurrentSession, db: DbSession) -> list[ProjectOut]:
    projects = db.scalars(
        visible_projects(active.user).order_by(InspectionProject.created_at.desc())
    )
    return [_project_out(db, active.user, project) for project in projects]


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate, active: CurrentSession, db: DbSession, ip: ClientIp, user_agent: UserAgent
) -> ProjectOut:
    if active.user.organization_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "권한이 없습니다.")
    project = InspectionProject(
        organization_id=active.user.organization_id,
        owner_user_id=active.user.id,
        visibility=body.visibility,
        project_name=body.project_name.strip(),
    )
    db.add(project)
    db.flush()
    audit.record(
        db,
        "project.created",
        organization_id=project.organization_id,
        actor_user_id=active.user.id,
        target_type="project",
        target_id=project.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"visibility": project.visibility},
    )
    db.refresh(project)
    return _project_out(db, active.user, project)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ProjectOut:
    project = authorize(db, active.user, project_id, "view", ip=ip, user_agent=user_agent)
    return _project_out(db, active.user, project)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ProjectOut:
    project = authorize(db, active.user, project_id, "manage", ip=ip, user_agent=user_agent)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "project_name" in changes:
        project.project_name = changes["project_name"].strip()
    if "visibility" in changes and changes["visibility"] != project.visibility:
        audit.record(
            db,
            "project.visibility_changed",
            organization_id=project.organization_id,
            actor_user_id=active.user.id,
            target_type="project",
            target_id=project.id,
            ip=ip,
            user_agent=user_agent,
            metadata={"from": project.visibility, "to": changes["visibility"]},
        )
        project.visibility = changes["visibility"]
    db.flush()
    db.refresh(project)
    return _project_out(db, active.user, project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    active: RecentlyAuthenticated,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> Response:
    project = authorize(db, active.user, project_id, "delete", ip=ip, user_agent=user_agent)
    prefix = project_prefix(project.organization_id, project.id)
    audit.record(
        db,
        "project.deleted",
        organization_id=project.organization_id,
        actor_user_id=active.user.id,
        target_type="project",
        target_id=project.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"project_name": project.project_name},
    )
    db.delete(project)
    # Files go after the database rows are committed; the prefix is server-derived.
    after_commit(db, store.delete_prefix, prefix)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/transfer", response_model=ProjectOut)
def transfer_project(
    project_id: uuid.UUID,
    body: ProjectTransfer,
    active: RecentlyAuthenticated,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ProjectOut:
    project = authorize(db, active.user, project_id, "transfer", ip=ip, user_agent=user_agent)
    new_owner = db.get(User, body.owner_user_id)
    if (
        new_owner is None
        or new_owner.organization_id != project.organization_id
        or new_owner.status != "active"
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "사용자를 찾을 수 없습니다.")
    previous = project.owner_user_id
    project.owner_user_id = new_owner.id
    audit.record(
        db,
        "project.ownership_transferred",
        organization_id=project.organization_id,
        actor_user_id=active.user.id,
        target_type="project",
        target_id=project.id,
        ip=ip,
        user_agent=user_agent,
        metadata={"from": str(previous), "to": str(new_owner.id)},
    )
    db.flush()
    db.refresh(project)
    return _project_out(db, active.user, project)


# --- Businesses ---------------------------------------------------------------------


@router.get("/projects/{project_id}/businesses", response_model=list[BusinessOut])
def list_businesses(
    project_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> list[BusinessOut]:
    authorize(db, active.user, project_id, "view", ip=ip, user_agent=user_agent)
    businesses = db.scalars(
        select(Business).where(Business.project_id == project_id).order_by(Business.created_at)
    )
    return [_business_out(db, business) for business in businesses]


@router.post(
    "/projects/{project_id}/businesses",
    response_model=BusinessOut,
    status_code=status.HTTP_201_CREATED,
)
def create_business(
    project_id: uuid.UUID,
    body: BusinessCreate,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> BusinessOut:
    authorize(db, active.user, project_id, "edit", ip=ip, user_agent=user_agent)
    business = Business(project_id=project_id, **body.model_dump())
    db.add(business)
    try:
        db.flush()
    except IntegrityError as error:
        raise _conflict(error, "이미 사용 중인 사업 코드입니다.") from error
    db.refresh(business)
    return _business_out(db, business)


@router.get("/businesses/{business_id}", response_model=BusinessOut)
def get_business(
    business_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> BusinessOut:
    return _business_out(db, load_business(db, active.user, business_id, "view", ip, user_agent))


@router.patch("/businesses/{business_id}", response_model=BusinessOut)
def update_business(
    business_id: uuid.UUID,
    body: BusinessFields,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> BusinessOut:
    business = load_business(db, active.user, business_id, "edit", ip, user_agent)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field in ("business_code", "business_name") and value is None:
            continue
        setattr(business, field, value)
    try:
        db.flush()
    except IntegrityError as error:
        raise _conflict(error, "이미 사용 중인 사업 코드입니다.") from error
    db.refresh(business)
    return _business_out(db, business)


@router.delete("/businesses/{business_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_business(
    business_id: uuid.UUID,
    active: CurrentSession,
    db: DbSession,
    store: Store,
    ip: ClientIp,
    user_agent: UserAgent,
) -> Response:
    business = load_business(db, active.user, business_id, "edit", ip, user_agent)
    project = db.get(InspectionProject, business.project_id)
    assert project is not None
    group_ids = list(
        db.scalars(select(ReportGroup.id).where(ReportGroup.business_id == business.id))
    )
    prefixes = [
        report_group_prefix(project.organization_id, project.id, group_id) for group_id in group_ids
    ] + [
        report_prefix(project.organization_id, project.id, report_id)
        for report_id in db.scalars(select(Report.id).where(Report.report_group_id.in_(group_ids)))
    ]
    audit.record(
        db,
        "business.deleted",
        organization_id=project.organization_id,
        actor_user_id=active.user.id,
        target_type="business",
        target_id=business.id,
        ip=ip,
        user_agent=user_agent,
    )
    db.delete(business)
    for prefix in prefixes:
        after_commit(db, store.delete_prefix, prefix)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class DuplicateProject(BaseModel):
    visibility: Visibility


class DuplicateBusiness(BaseModel):
    target_project_id: uuid.UUID | None = None


@router.post(
    "/projects/{project_id}/duplicate",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_project(
    project_id: uuid.UUID,
    body: DuplicateProject,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> ProjectOut:
    """Copies businesses and report inputs; videos and defects are not copied."""
    project = authorize(db, active.user, project_id, "view", ip=ip, user_agent=user_agent)
    copy = report_service.duplicate_project(db, project, active.user.id, body.visibility)
    db.refresh(copy)
    return _project_out(db, active.user, copy)


@router.post(
    "/businesses/{business_id}/duplicate",
    response_model=BusinessOut,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_business(
    business_id: uuid.UUID,
    body: DuplicateBusiness,
    active: CurrentSession,
    db: DbSession,
    ip: ClientIp,
    user_agent: UserAgent,
) -> BusinessOut:
    business = load_business(db, active.user, business_id, "view", ip, user_agent)
    target_project_id = body.target_project_id or business.project_id
    authorize(db, active.user, target_project_id, "edit", ip=ip, user_agent=user_agent)
    copy = report_service.duplicate_business(db, business, target_project_id)
    db.refresh(copy)
    return _business_out(db, copy)
