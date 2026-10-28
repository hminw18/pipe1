"""Row-level security against the permission matrix in docs/Web_Auth_Spec.md §2.

Setup runs as the table owner (policies off); every assertion runs as the app role
with an access context, exactly as request handlers will.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, create_engine, delete, func, select, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from pipe1_server.db import set_access_context
from pipe1_server.ids import uuid7
from pipe1_server.models.auth import Organization, User
from pipe1_server.models.inspection import (
    Business,
    InspectionProject,
    Report,
    ReportDefect,
    ReportGroup,
    ReportVideo,
)


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str


@dataclass(frozen=True)
class World:
    a_owner: Actor
    a_member: Actor
    a_admin: Actor
    b_user: Actor
    a_private_project: uuid.UUID
    a_shared_project: uuid.UUID
    b_shared_project: uuid.UUID
    a_private_business: uuid.UUID
    a_shared_business: uuid.UUID
    a_shared_report: uuid.UUID


def _add_project_tree(
    session: Session, owner: Actor, visibility: str, name: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    project = InspectionProject(
        id=uuid7(),
        organization_id=owner.organization_id,
        owner_user_id=owner.user_id,
        visibility=visibility,
        project_name=name,
    )
    business = Business(
        id=uuid7(), project_id=project.id, business_code="B001", business_name="사업1"
    )
    group = ReportGroup(
        id=uuid7(),
        project_id=project.id,
        business_id=business.id,
        report_number="R001",
        pipe_number="P-001",
    )
    report = Report(
        id=uuid7(),
        project_id=project.id,
        report_group_id=group.id,
        version_number=1,
        version_name="v1",
    )
    video = ReportVideo(
        id=uuid7(),
        project_id=project.id,
        report_id=report.id,
        original_object_key=f"test/{uuid7()}/original.avi",
        original_filename="original.avi",
    )
    defect = ReportDefect(
        id=uuid7(),
        project_id=project.id,
        report_id=report.id,
        video_id=video.id,
        timestamp_ms=1000,
        image_object_key=f"test/{uuid7()}/capture.png",
        drive_direction="순주행",
        distance_m=1.5,
    )
    for row in (project, business, group, report, video, defect):
        session.add(row)
        session.flush()
    return project.id, business.id, report.id


@pytest.fixture(scope="module")
def world(owner_engine: Engine) -> World:
    with Session(owner_engine) as session, session.begin():
        org_a = Organization(id=uuid7(), name="Company A")
        org_b = Organization(id=uuid7(), name="Company B")
        session.add_all([org_a, org_b])
        session.flush()

        def add_user(org: Organization, role: str) -> Actor:
            user = User(
                id=uuid7(),
                organization_id=org.id,
                email=f"{uuid7().hex}@example.com",
                role=role,
                status="active",
            )
            session.add(user)
            session.flush()
            return Actor(user.id, org.id, role)

        a_owner = add_user(org_a, "inspector")
        a_member = add_user(org_a, "inspector")
        a_admin = add_user(org_a, "org_admin")
        b_user = add_user(org_b, "inspector")

        a_private, a_private_business, _ = _add_project_tree(
            session, a_owner, "private", "A private"
        )
        a_shared, a_shared_business, a_shared_report = _add_project_tree(
            session, a_owner, "org", "A shared"
        )
        b_shared, _, _ = _add_project_tree(session, b_user, "org", "B shared")

    return World(
        a_owner=a_owner,
        a_member=a_member,
        a_admin=a_admin,
        b_user=b_user,
        a_private_project=a_private,
        a_shared_project=a_shared,
        b_shared_project=b_shared,
        a_private_business=a_private_business,
        a_shared_business=a_shared_business,
        a_shared_report=a_shared_report,
    )


@contextmanager
def acting_as(app_engine: Engine, actor: Actor | None) -> Iterator[Session]:
    """One app-role transaction with the actor's access context; always rolled back."""
    with Session(app_engine) as session:
        session.begin()
        if actor is not None:
            set_access_context(
                session,
                organization_id=actor.organization_id,
                user_id=actor.user_id,
                role=actor.role,
            )
        try:
            yield session
        finally:
            session.rollback()


def _visible_projects(session: Session) -> set[uuid.UUID]:
    return set(session.scalars(select(InspectionProject.id)))


def _visible_businesses(session: Session) -> set[uuid.UUID]:
    return set(session.scalars(select(Business.id)))


def _new_business(project_id: uuid.UUID) -> Business:
    return Business(
        id=uuid7(), project_id=project_id, business_code=uuid7().hex, business_name="new"
    )


# --- View -------------------------------------------------------------------


def test_owner_sees_own_private_and_shared_projects(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_owner) as session:
        assert _visible_projects(session) == {world.a_private_project, world.a_shared_project}


def test_member_sees_only_shared_projects_of_own_org(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_member) as session:
        assert _visible_projects(session) == {world.a_shared_project}
        assert _visible_businesses(session) == {world.a_shared_business}


def test_org_admin_sees_private_projects_of_own_org(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_admin) as session:
        assert _visible_projects(session) == {world.a_private_project, world.a_shared_project}


def test_other_org_sees_nothing_of_org_a(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.b_user) as session:
        assert _visible_projects(session) == {world.b_shared_project}
        for model in (Business, ReportGroup, Report, ReportVideo, ReportDefect):
            project_ids = set(session.scalars(select(model.project_id)))
            assert project_ids == {world.b_shared_project}, model.__tablename__


def test_no_access_context_sees_nothing(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, None) as session:
        for model in (InspectionProject, Business, Report, ReportDefect):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_platform_admin_sees_no_project_data(app_engine: Engine, world: World) -> None:
    with Session(app_engine) as session:
        session.begin()
        set_access_context(session, organization_id=None, user_id=uuid7(), role="platform_admin")
        for model in (InspectionProject, Business, Report, ReportDefect):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        session.rollback()


def test_access_context_does_not_leak_to_next_transaction(
    migrated: dict[str, URL], world: World
) -> None:
    # A single pooled connection, so both transactions run on the same connection.
    single = create_engine(migrated["app"], pool_size=1, max_overflow=0)
    with Session(single) as session:
        with session.begin():
            set_access_context(
                session,
                organization_id=world.a_owner.organization_id,
                user_id=world.a_owner.user_id,
                role=world.a_owner.role,
            )
            assert _visible_projects(session)
        with session.begin():
            assert _visible_projects(session) == set()
    single.dispose()


# --- Edit -------------------------------------------------------------------


def test_member_can_edit_shared_project_contents(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_member) as session:
        session.add(_new_business(world.a_shared_project))
        session.flush()
        result = session.execute(
            update(Report).where(Report.id == world.a_shared_report).values(inspector="member edit")
        )
        assert result.rowcount == 1


def test_member_cannot_add_to_private_project(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_member) as session:
        session.add(_new_business(world.a_private_project))
        with pytest.raises(DBAPIError, match="row-level security"):
            session.flush()


def test_other_org_cannot_add_to_shared_project(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.b_user) as session:
        session.add(_new_business(world.a_shared_project))
        with pytest.raises(DBAPIError, match="row-level security"):
            session.flush()


def test_other_org_cannot_update_or_delete_contents(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.b_user) as session:
        updated = session.execute(
            update(Business)
            .where(Business.id == world.a_shared_business)
            .values(business_name="hacked")
        )
        deleted = session.execute(delete(Business).where(Business.id == world.a_shared_business))
        assert updated.rowcount == 0
        assert deleted.rowcount == 0


# --- Visibility, delete, ownership ----------------------------------------------


def test_member_cannot_change_visibility_or_delete_shared_project(
    app_engine: Engine, world: World
) -> None:
    with acting_as(app_engine, world.a_member) as session:
        updated = session.execute(
            update(InspectionProject)
            .where(InspectionProject.id == world.a_shared_project)
            .values(visibility="private")
        )
        deleted = session.execute(
            delete(InspectionProject).where(InspectionProject.id == world.a_shared_project)
        )
        assert updated.rowcount == 0
        assert deleted.rowcount == 0


def test_owner_can_change_visibility_and_delete(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_owner) as session:
        updated = session.execute(
            update(InspectionProject)
            .where(InspectionProject.id == world.a_shared_project)
            .values(visibility="private")
        )
        deleted = session.execute(
            delete(InspectionProject).where(InspectionProject.id == world.a_private_project)
        )
        assert updated.rowcount == 1
        assert deleted.rowcount == 1


def test_org_admin_can_change_visibility_and_delete(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_admin) as session:
        updated = session.execute(
            update(InspectionProject)
            .where(InspectionProject.id == world.a_private_project)
            .values(visibility="org")
        )
        deleted = session.execute(
            delete(InspectionProject).where(InspectionProject.id == world.a_shared_project)
        )
        assert updated.rowcount == 1
        assert deleted.rowcount == 1


def test_owner_cannot_transfer_ownership(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_owner) as session:
        with pytest.raises(DBAPIError, match="row-level security"):
            session.execute(
                update(InspectionProject)
                .where(InspectionProject.id == world.a_shared_project)
                .values(owner_user_id=world.a_member.user_id)
            )


def test_org_admin_can_transfer_ownership_within_org(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_admin) as session:
        result = session.execute(
            update(InspectionProject)
            .where(InspectionProject.id == world.a_private_project)
            .values(owner_user_id=world.a_member.user_id)
        )
        assert result.rowcount == 1


def test_org_admin_cannot_transfer_ownership_to_other_org(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_admin) as session:
        with pytest.raises(IntegrityError, match="fk_inspection_projects_owner_user_id"):
            session.execute(
                update(InspectionProject)
                .where(InspectionProject.id == world.a_private_project)
                .values(owner_user_id=world.b_user.user_id)
            )


# --- Create -----------------------------------------------------------------


def test_user_can_create_own_project(app_engine: Engine, world: World) -> None:
    with acting_as(app_engine, world.a_member) as session:
        project = InspectionProject(
            id=uuid7(),
            organization_id=world.a_member.organization_id,
            owner_user_id=world.a_member.user_id,
            visibility="private",
            project_name="mine",
        )
        session.add(project)
        session.flush()
        assert project.id in _visible_projects(session)


@pytest.mark.parametrize("field", ["owner", "organization"])
def test_user_cannot_create_project_for_someone_else(
    app_engine: Engine, world: World, field: str
) -> None:
    with acting_as(app_engine, world.a_member) as session:
        session.add(
            InspectionProject(
                id=uuid7(),
                organization_id=(
                    world.b_user.organization_id
                    if field == "organization"
                    else world.a_member.organization_id
                ),
                owner_user_id=world.a_owner.user_id if field == "owner" else world.a_member.user_id,
                visibility="org",
                project_name="spoofed",
            )
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            session.flush()


# --- Schema integrity ---------------------------------------------------------


def test_child_project_id_must_match_parent(owner_engine: Engine, world: World) -> None:
    with Session(owner_engine) as session:
        session.add(
            ReportGroup(
                id=uuid7(),
                project_id=world.a_private_project,
                business_id=world.a_shared_business,
                report_number="R-mismatch",
                pipe_number="P",
            )
        )
        with pytest.raises(IntegrityError, match="fk_report_groups_business_id_project_id"):
            session.flush()
