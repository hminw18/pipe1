"""Authorization matrix (docs/Web_Auth_Spec.md §14): every inspection endpoint, run as every
kind of actor, against a private and a shared project. Expected statuses come from the
permission table in §2, written out independently of the policy code."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from pipe1_server.ids import uuid7
from pipe1_server.models.auth import User
from pipe1_server.models.inspection import (
    Business,
    InspectionProject,
    Report,
    ReportGroup,
)
from pipe1_server.services.reports import create_report_group
from tests.api_support import (
    Clock,
    TestUser,
    create_user,
    csrf_headers,
    full_login,
    make_client,
    make_settings,
)

ACTORS = (
    "owner",
    "member",
    "admin",
    "other_org",
    "platform_admin",
    "deactivated",
    "anonymous",
)


@dataclass
class Actors:
    users: dict[str, TestUser]
    clients: dict[str, TestClient]
    csrf: dict[str, dict[str, str]]


@dataclass(frozen=True)
class Tree:
    project: uuid.UUID
    business: uuid.UUID
    group: uuid.UUID
    report: uuid.UUID


@pytest.fixture(scope="module")
def actors(app_engine: Engine, owner_engine: Engine):
    clock = Clock()
    settings = make_settings()
    owner = create_user(owner_engine)
    org_id = owner.organization_id

    def same_org(role: str, **kwargs) -> TestUser:
        user = create_user(owner_engine, role=role, **kwargs)
        with Session(owner_engine) as session, session.begin():
            session.execute(update(User).where(User.id == user.id).values(organization_id=org_id))
        return TestUser(user.id, user.email, org_id, user.totp_secret)

    users = {
        "owner": owner,
        "member": same_org("inspector"),
        "admin": same_org("org_admin", with_totp=True),
        "other_org": create_user(owner_engine),
        "platform_admin": create_user(owner_engine, role="platform_admin", with_totp=True),
        "deactivated": same_org("inspector"),
    }
    clients: dict[str, TestClient] = {}
    csrf: dict[str, dict[str, str]] = {}
    for name in ACTORS:
        client = make_client(app_engine, clock, settings)
        client.__enter__()
        clients[name] = client
        if name in users:
            full_login(client, users[name], clock)
            csrf[name] = csrf_headers(client)
        else:
            csrf[name] = {}
    with Session(owner_engine) as session, session.begin():
        session.execute(
            update(User).where(User.id == users["deactivated"].id).values(status="disabled")
        )
    yield Actors(users, clients, csrf)
    for client in clients.values():
        client.__exit__(None, None, None)


def make_tree(owner_engine: Engine, owner: TestUser, visibility: str) -> Tree:
    with Session(owner_engine) as session, session.begin():
        project = InspectionProject(
            id=uuid7(),
            organization_id=owner.organization_id,
            owner_user_id=owner.id,
            visibility=visibility,
            project_name="matrix",
        )
        session.add(project)
        session.flush()
        business = Business(
            id=uuid7(), project_id=project.id, business_code="B1", business_name="사업"
        )
        session.add(business)
        session.flush()
        report = create_report_group(session, business, "R1", "P1")
        session.flush()
        group = session.get(ReportGroup, report.report_group_id)
        return Tree(project.id, business.id, group.id, report.id)


def add_second_version(owner_engine: Engine, tree: Tree) -> None:
    with Session(owner_engine) as session, session.begin():
        session.add(
            Report(
                project_id=tree.project,
                report_group_id=tree.group,
                version_number=2,
                version_name="v2",
            )
        )


# (method, path, json body, required action, success status)
Endpoint = tuple[str, Callable[[Tree], str], Callable[[Actors], dict] | None, str, int]
ENDPOINTS: dict[str, Endpoint] = {
    "get project": ("GET", lambda t: f"/api/projects/{t.project}", None, "view", 200),
    "list businesses": (
        "GET",
        lambda t: f"/api/projects/{t.project}/businesses",
        None,
        "view",
        200,
    ),
    "get business": ("GET", lambda t: f"/api/businesses/{t.business}", None, "view", 200),
    "list reports": ("GET", lambda t: f"/api/businesses/{t.business}/reports", None, "view", 200),
    "get report": ("GET", lambda t: f"/api/reports/{t.report}", None, "view", 200),
    "duplicate project": (
        "POST",
        lambda t: f"/api/projects/{t.project}/duplicate",
        lambda a: {"visibility": "private"},
        "view",
        201,
    ),
    "create business": (
        "POST",
        lambda t: f"/api/projects/{t.project}/businesses",
        lambda a: {"business_code": f"N{uuid.uuid4().hex[:6]}", "business_name": "새 사업"},
        "edit",
        201,
    ),
    "update business": (
        "PATCH",
        lambda t: f"/api/businesses/{t.business}",
        lambda a: {"client": "x"},
        "edit",
        200,
    ),
    "duplicate business": (
        "POST",
        lambda t: f"/api/businesses/{t.business}/duplicate",
        lambda a: {},
        "edit",
        201,
    ),
    "delete business": ("DELETE", lambda t: f"/api/businesses/{t.business}", None, "edit", 204),
    "create report": (
        "POST",
        lambda t: f"/api/businesses/{t.business}/reports",
        lambda a: {"report_number": f"R{uuid.uuid4().hex[:6]}", "pipe_number": "P"},
        "edit",
        201,
    ),
    "update report": (
        "PATCH",
        lambda t: f"/api/reports/{t.report}",
        lambda a: {"inspector": "kim"},
        "edit",
        200,
    ),
    "update pipe": (
        "PATCH",
        lambda t: f"/api/reports/{t.report}/pipe-information",
        lambda a: {"length_m": 10},
        "edit",
        200,
    ),
    "update manhole": (
        "PATCH",
        lambda t: f"/api/reports/{t.report}/manholes/upstream",
        lambda a: {"manhole_number": "M1"},
        "edit",
        200,
    ),
    "update survey": (
        "PATCH",
        lambda t: f"/api/reports/{t.report}/actual-survey",
        lambda a: {"survey_content": "ok"},
        "edit",
        200,
    ),
    "create version": (
        "POST",
        lambda t: f"/api/report-groups/{t.group}/versions",
        lambda a: {},
        "edit",
        201,
    ),
    "duplicate report": (
        "POST",
        lambda t: f"/api/report-groups/{t.group}/duplicate",
        lambda a: {},
        "edit",
        201,
    ),
    "delete version": ("DELETE", lambda t: f"/api/reports/{t.report}", None, "edit", 204),
    "delete report": ("DELETE", lambda t: f"/api/report-groups/{t.group}", None, "edit", 204),
    "change visibility": (
        "PATCH",
        lambda t: f"/api/projects/{t.project}",
        lambda a: {"visibility": "org"},
        "manage",
        200,
    ),
    "delete project": ("DELETE", lambda t: f"/api/projects/{t.project}", None, "delete", 204),
    "transfer project": (
        "POST",
        lambda t: f"/api/projects/{t.project}/transfer",
        lambda a: {"owner_user_id": str(a.users["member"].id)},
        "transfer",
        200,
    ),
}


def expected_status(actor: str, visibility: str, action: str, success: int) -> int:
    """docs/Web_Auth_Spec.md §2, independently of security/authorization.py."""
    if actor in ("anonymous", "deactivated"):
        return 401
    if actor in ("other_org", "platform_admin"):
        return 404
    viewable = actor in ("owner", "admin") or visibility == "org"
    if not viewable:
        return 404
    allowed = {
        "view": True,
        "edit": True,
        "manage": actor in ("owner", "admin"),
        "delete": actor in ("owner", "admin"),
        "transfer": actor == "admin",
    }[action]
    return success if allowed else 403


@pytest.mark.parametrize("visibility", ["private", "org"])
@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
def test_authorization_matrix(
    actors: Actors, owner_engine: Engine, endpoint: str, visibility: str
) -> None:
    method, path, body, action, success = ENDPOINTS[endpoint]
    failures = []
    for actor in ACTORS:
        tree = make_tree(owner_engine, actors.users["owner"], visibility)
        if endpoint == "delete version":
            add_second_version(owner_engine, tree)
        client = actors.clients[actor]
        response = client.request(
            method,
            path(tree),
            json=body(actors) if body else None,
            headers=actors.csrf[actor],
        )
        expected = expected_status(actor, visibility, action, success)
        if response.status_code != expected:
            failures.append(f"{actor}: got {response.status_code}, expected {expected}")
    assert not failures, failures
