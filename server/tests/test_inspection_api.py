"""Behaviour of the projects / businesses / reports API (docs/Product_Requirements.md)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pipe1_server.models.auth import AuditEvent
from pipe1_server.models.inspection import ReportDefect, ReportVideo
from pipe1_server.storage import LocalObjectStore
from tests.api_support import (
    LOCAL_STORAGE_DIR,
    Clock,
    create_user,
    csrf_headers,
    full_login,
    login,
)


@pytest.fixture
def owner_client(client: TestClient, owner_engine: Engine):
    user = create_user(owner_engine)
    login(client, user)
    client.headers.update(csrf_headers(client))
    return client, user


def _project(client: TestClient, visibility: str = "org") -> dict:
    response = client.post(
        "/api/projects", json={"project_name": "프로젝트", "visibility": visibility}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _business(client: TestClient, project_id: str, code: str = "B001") -> dict:
    response = client.post(
        f"/api/projects/{project_id}/businesses",
        json={"business_code": code, "business_name": "사업1"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _report(client: TestClient, business_id: str, number: str = "R001") -> dict:
    response = client.post(
        f"/api/businesses/{business_id}/reports",
        json={"report_number": number, "pipe_number": "P-001"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_project_requires_explicit_visibility(owner_client) -> None:
    client, _ = owner_client
    assert client.post("/api/projects", json={"project_name": "x"}).status_code == 422


def test_new_report_has_both_manholes_and_survey_defaults(owner_client) -> None:
    client, _ = owner_client
    business = _business(client, _project(client)["id"])
    report = _report(client, business["id"])
    assert set(report["manholes"]) == {"upstream", "downstream"}
    assert report["actual_survey"]["start_undriven_reason"] == "없음"
    assert report["version_name"] == "v1"
    assert report["export_ready"] is False
    assert {"survey_date", "buried_years", "length_m", "total_drive_distance_m"} <= set(
        report["export_missing"]
    )


def test_pipe_values_are_derived(owner_client) -> None:
    client, _ = owner_client
    report = _report(client, _business(client, _project(client)["id"])["id"])
    url = f"/api/reports/{report['id']}/pipe-information"
    partial = client.patch(url, json={"length_m": 100, "total_drive_distance_m": 80}).json()
    assert partial["pipe_information"]["undriven_distance_m"] == 20
    assert partial["pipe_information"]["is_completed"] is False
    done = client.patch(url, json={"total_drive_distance_m": 100}).json()
    assert done["pipe_information"]["is_completed"] is True
    assert done["pipe_information"]["length_m"] == 100  # untouched by the partial update


def test_partial_updates_leave_other_fields_alone(owner_client) -> None:
    client, _ = owner_client
    report = _report(client, _business(client, _project(client)["id"])["id"])
    url = f"/api/reports/{report['id']}"
    client.patch(url, json={"inspector": "김조사", "survey_date": "2026-10-01"})
    updated = client.patch(url, json={"survey_date": "2026-10-02"}).json()
    assert updated["fields"]["inspector"] == "김조사"
    assert updated["fields"]["survey_date"] == "2026-10-02"
    cleared = client.patch(url, json={"inspector": None}).json()
    assert cleared["fields"]["inspector"] is None


def test_export_readiness(owner_client) -> None:
    client, _ = owner_client
    report = _report(client, _business(client, _project(client)["id"])["id"])
    client.patch(
        f"/api/reports/{report['id']}", json={"survey_date": "2026-10-01", "buried_years": "10"}
    )
    ready = client.patch(
        f"/api/reports/{report['id']}/pipe-information",
        json={"length_m": 50, "total_drive_distance_m": 40},
    ).json()
    assert ready["export_ready"] is True and ready["export_missing"] == []


def test_report_number_unique_per_business(owner_client) -> None:
    client, _ = owner_client
    business = _business(client, _project(client)["id"])
    _report(client, business["id"], "R1")
    clash = client.post(
        f"/api/businesses/{business['id']}/reports",
        json={"report_number": "R1", "pipe_number": "P"},
    )
    assert clash.status_code == 409


def test_duplicate_report_copies_inputs_with_new_number(owner_client) -> None:
    client, _ = owner_client
    business = _business(client, _project(client)["id"])
    report = _report(client, business["id"], "R1")
    client.patch(f"/api/reports/{report['id']}", json={"inspector": "이조사"})
    copy = client.post(f"/api/report-groups/{report['report_group_id']}/duplicate", json={}).json()
    assert copy["report_number"] == "R1 복사본"
    assert copy["fields"]["inspector"] == "이조사"
    assert copy["has_video"] is False and copy["defect_count"] == 0


def _attach_video_and_defect(owner_engine: Engine, report: dict) -> tuple[str, str]:
    store = LocalObjectStore(Path(LOCAL_STORAGE_DIR), "https://testserver", "test-csrf-key")
    video_key = f"test/{uuid.uuid4()}/original.avi"
    capture = f"test/{uuid.uuid4()}/capture.png"
    store.put_bytes(video_key, b"video", "video/x-msvideo")
    store.put_bytes(capture, b"png-bytes", "image/png")
    with Session(owner_engine) as session, session.begin():
        video = ReportVideo(
            project_id=uuid.UUID(report["project_id"]),
            report_id=uuid.UUID(report["id"]),
            original_object_key=video_key,
            original_filename="original.avi",
        )
        session.add(video)
        session.flush()
        session.add(
            ReportDefect(
                project_id=video.project_id,
                report_id=video.report_id,
                video_id=video.id,
                timestamp_ms=1200,
                image_object_key=capture,
                drive_direction="순주행",
                distance_m=3.5,
                defect_item="파손",
                grade="중",
            )
        )
    return video_key, capture


def test_new_version_shares_video_and_copies_each_capture(
    owner_client, owner_engine: Engine
) -> None:
    client, _ = owner_client
    report = _report(client, _business(client, _project(client)["id"])["id"])
    video_key, capture = _attach_video_and_defect(owner_engine, report)
    v2 = client.post(f"/api/report-groups/{report['report_group_id']}/versions", json={}).json()
    assert v2["version_name"] == "v2" and v2["defect_count"] == 1 and v2["has_video"]
    with Session(owner_engine) as session:
        new_defect = session.scalar(
            select(ReportDefect).where(ReportDefect.report_id == uuid.UUID(v2["id"]))
        )
        new_video = session.scalar(
            select(ReportVideo).where(ReportVideo.report_id == uuid.UUID(v2["id"]))
        )
    assert new_video.original_object_key == video_key
    assert new_defect.image_object_key != capture
    store = LocalObjectStore(Path(LOCAL_STORAGE_DIR), "https://testserver", "test-csrf-key")
    assert store.exists(new_defect.image_object_key)

    # Deleting v2 keeps the shared video (v1 still references it) and v1's capture.
    assert client.delete(f"/api/reports/{v2['id']}").status_code == 204
    assert store.exists(video_key)
    assert store.exists(capture)
    assert not store.exists(new_defect.image_object_key)


def test_last_version_cannot_be_deleted(owner_client) -> None:
    client, _ = owner_client
    report = _report(client, _business(client, _project(client)["id"])["id"])
    assert client.delete(f"/api/reports/{report['id']}").status_code == 409


def test_project_delete_removes_stored_files(owner_client, clock: Clock) -> None:
    client, user = owner_client
    project = _project(client)
    store = LocalObjectStore(Path(LOCAL_STORAGE_DIR), "https://testserver", "test-csrf-key")
    key = f"org/{user.organization_id}/project/{project['id']}/report/x/captures/a.png"
    store.put_bytes(key, b"x", "image/png")
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert not store.exists(key)


def test_project_delete_requires_recent_authentication(owner_client, clock: Clock) -> None:
    client, _ = owner_client
    project = _project(client)
    clock.advance(minutes=11)
    response = client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 403
    assert client.get(f"/api/projects/{project['id']}").status_code == 200


def test_project_list_scope(client: TestClient, owner_engine: Engine, app_engine) -> None:
    from tests.api_support import make_client, make_settings

    owner = create_user(owner_engine)
    login(client, owner)
    client.headers.update(csrf_headers(client))
    private = _project(client, "private")["id"]
    shared = _project(client, "org")["id"]

    from sqlalchemy import update

    from pipe1_server.models.auth import User

    member = create_user(owner_engine)
    with Session(owner_engine) as session, session.begin():
        session.execute(
            update(User).where(User.id == member.id).values(organization_id=owner.organization_id)
        )
    with make_client(app_engine, Clock(), make_settings()) as member_client:
        login(member_client, member)
        visible = {p["id"] for p in member_client.get("/api/projects").json()}
    assert shared in visible and private not in visible


def test_visibility_change_and_admin_access_are_audited(
    client: TestClient, owner_engine: Engine, app_engine, clock: Clock
) -> None:
    from tests.api_support import make_client, make_settings

    owner = create_user(owner_engine)
    login(client, owner)
    client.headers.update(csrf_headers(client))
    project = _project(client, "private")["id"]
    client.patch(f"/api/projects/{project}", json={"visibility": "org"})

    from sqlalchemy import update

    from pipe1_server.models.auth import User

    admin = create_user(owner_engine, role="org_admin", with_totp=True)
    with Session(owner_engine) as session, session.begin():
        session.execute(
            update(User).where(User.id == admin.id).values(organization_id=owner.organization_id)
        )
    with make_client(app_engine, clock, make_settings()) as admin_client:
        full_login(admin_client, admin, clock)
        assert admin_client.get(f"/api/projects/{project}").status_code == 200

    with Session(owner_engine) as session:
        actions = list(
            session.scalars(select(AuditEvent.action).where(AuditEvent.target_id == project))
        )
    assert "project.visibility_changed" in actions
    assert "project.admin_access" in actions
