"""End-to-end video pipeline with real ffmpeg: signed upload, worker processing (inline),
frame-accurate capture, replacement, rejection of bad files, exports, stop analysis."""

from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import Engine

from tests.api_support import (
    Clock,
    create_user,
    csrf_headers,
    login,
    make_client,
    make_settings,
)

FPS = 10
FRAMES = 60


def make_video(path: Path) -> Path:
    """Each frame has a distinct brightness, so a captured frame identifies its index."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), float(FPS), (320, 240))
    for index in range(FRAMES):
        frame = np.full((240, 320, 3), index * 4, dtype=np.uint8)
        cv2.putText(
            frame, f"{index:02d}", (100, 140), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3
        )
        writer.write(frame)
    writer.release()
    return path


def _path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


@pytest.fixture
def report(client: TestClient, owner_engine: Engine) -> dict:
    login(client, create_user(owner_engine))
    client.headers.update(csrf_headers(client))
    project = client.post("/api/projects", json={"project_name": "P", "visibility": "org"}).json()
    business = client.post(
        f"/api/projects/{project['id']}/businesses",
        json={"business_code": "B1", "business_name": "사업"},
    ).json()
    return client.post(
        f"/api/businesses/{business['id']}/reports",
        json={"report_number": "R001", "pipe_number": "P-001"},
    ).json()


def upload(client: TestClient, report_id: str, path: Path, name: str | None = None) -> dict:
    data = path.read_bytes()
    session = client.post(
        f"/api/reports/{report_id}/video/uploads",
        json={"filename": name or path.name, "size_bytes": len(data)},
    )
    assert session.status_code == 201, session.text
    body = session.json()
    assert body["protocol"] == "put"
    put = client.put(_path_and_query(body["url"]), content=data, headers=body["headers"])
    assert put.status_code == 200, put.text
    done = client.post(f"/api/video-uploads/{body['upload_id']}/complete", json={})
    assert done.status_code == 200, done.text
    return client.get(f"/api/reports/{report_id}/video").json()


def test_upload_process_and_play(client: TestClient, report: dict, tmp_path: Path) -> None:
    video = upload(client, report["id"], make_video(tmp_path / "v.avi"))
    assert video["transcode_status"] == "ready", video
    assert video["duration_seconds"] == pytest.approx(FRAMES / FPS, abs=0.2)
    assert (video["width"], video["height"]) == (320, 240)
    playback = client.get(_path_and_query(video["playback_url"]))
    assert playback.status_code == 200 and playback.content[4:8] == b"ftyp"
    assert client.get(_path_and_query(video["thumbnail_url"])).status_code == 200


def test_capture_is_frame_accurate(client: TestClient, report: dict, tmp_path: Path) -> None:
    upload(client, report["id"], make_video(tmp_path / "v.avi"))
    response = client.post(
        f"/api/reports/{report['id']}/defects",
        json={"timestamp_ms": 2500, "drive_direction": "순주행", "distance_m": 4.2},
    )
    assert response.status_code == 201, response.text
    image = client.get(_path_and_query(response.json()["image_url"]))
    frame = cv2.imdecode(np.frombuffer(image.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    # Frame 25 (2.5 s at 10 fps) has background brightness 100.
    assert abs(int(np.median(frame)) - 25 * 4) <= 3


def test_two_captures_at_same_time_have_separate_images(
    client: TestClient, report: dict, tmp_path: Path
) -> None:
    upload(client, report["id"], make_video(tmp_path / "v.avi"))
    payload = {"timestamp_ms": 1000, "drive_direction": "순주행", "distance_m": 1.0}
    first = client.post(f"/api/reports/{report['id']}/defects", json=payload).json()
    second = client.post(f"/api/reports/{report['id']}/defects", json=payload).json()
    assert client.delete(f"/api/defects/{first['id']}").status_code == 204
    assert client.get(_path_and_query(second["image_url"])).status_code == 200


def test_capture_without_distance_or_roi_is_rejected_before_saving(
    client: TestClient, report: dict, tmp_path: Path
) -> None:
    upload(client, report["id"], make_video(tmp_path / "v.avi"))
    response = client.post(
        f"/api/reports/{report['id']}/defects",
        json={"timestamp_ms": 1000, "drive_direction": "순주행"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "distance_required"
    assert client.get(f"/api/reports/{report['id']}/defects").json() == []


@pytest.mark.parametrize(
    "fields",
    [
        {"item_category": "관로", "condition_item": "x", "defect_item": "y"},
        {"item_category": "관로", "defect_item": "존재하지않는결함"},
        {"grade": "대"},
    ],
)
def test_defect_classification_is_validated(
    client: TestClient, report: dict, tmp_path: Path, fields: dict
) -> None:
    upload(client, report["id"], make_video(tmp_path / "v.avi"))
    response = client.post(
        f"/api/reports/{report['id']}/defects",
        json={"timestamp_ms": 0, "drive_direction": "순주행", "distance_m": 1.0, **fields},
    )
    assert response.status_code == 422


def test_replacing_video_keeps_defects(client: TestClient, report: dict, tmp_path: Path) -> None:
    first = upload(client, report["id"], make_video(tmp_path / "a.avi"))
    client.post(
        f"/api/reports/{report['id']}/defects",
        json={"timestamp_ms": 500, "drive_direction": "순주행", "distance_m": 2.0},
    )
    old_playback = _path_and_query(first["playback_url"])
    second = upload(client, report["id"], make_video(tmp_path / "b.avi"), "b.avi")
    assert second["id"] == first["id"]
    assert second["original_filename"] == "b.avi"
    assert len(client.get(f"/api/reports/{report['id']}/defects").json()) == 1
    assert client.get(old_playback).status_code == 404  # replaced media deleted


def test_invalid_file_is_rejected_and_deleted(
    client: TestClient, report: dict, tmp_path: Path
) -> None:
    fake = tmp_path / "fake.mp4"
    fake.write_text("#EXTM3U\n#EXTINF:10.0,\n/etc/passwd\n#EXT-X-ENDLIST\n")
    video = upload(client, report["id"], fake)
    assert video["transcode_status"] == "failed"
    assert "지원하지 않거나" in video["processing_error"]
    jobs = client.get(f"/api/reports/{report['id']}/jobs?kind=process_video").json()
    assert jobs[0]["status"] == "failed"
    assert "/etc/passwd" not in (jobs[0]["error"] or "")


def test_upload_limits(client: TestClient, report: dict, app_engine, clock: Clock) -> None:
    wrong_type = client.post(
        f"/api/reports/{report['id']}/video/uploads",
        json={"filename": "evil.exe", "size_bytes": 10},
    )
    assert wrong_type.status_code == 422
    too_big = client.post(
        f"/api/reports/{report['id']}/video/uploads",
        json={"filename": "v.avi", "size_bytes": 10**15},
    )
    assert too_big.status_code == 413


def test_size_mismatch_is_rejected(client: TestClient, report: dict, tmp_path: Path) -> None:
    data = make_video(tmp_path / "v.avi").read_bytes()
    session = client.post(
        f"/api/reports/{report['id']}/video/uploads",
        json={"filename": "v.avi", "size_bytes": len(data) + 10},
    ).json()
    client.put(_path_and_query(session["url"]), content=data, headers=session["headers"])
    done = client.post(f"/api/video-uploads/{session['upload_id']}/complete", json={})
    assert done.status_code == 409


def test_tampered_or_oversized_upload_urls_are_refused(client: TestClient, report: dict) -> None:
    session = client.post(
        f"/api/reports/{report['id']}/video/uploads",
        json={"filename": "v.avi", "size_bytes": 10},
    ).json()
    url = _path_and_query(session["url"])
    tampered = url.replace("original.avi", "other.avi")
    assert client.put(tampered, content=b"x" * 10, headers=session["headers"]).status_code == 403
    assert client.put(url, content=b"x" * 11, headers=session["headers"]).status_code == 413


def test_exports(client: TestClient, report: dict, tmp_path: Path) -> None:
    upload(client, report["id"], make_video(tmp_path / "v.avi"))
    client.post(
        f"/api/reports/{report['id']}/defects",
        json={
            "timestamp_ms": 1200,
            "drive_direction": "순주행",
            "distance_m": 3.0,
            "item_category": "관로",
            "defect_item": "균열(길이)",
            "grade": "대",
            "memo": '=HYPERLINK("https://evil")',
        },
    )
    not_ready = client.post(f"/api/reports/{report['id']}/exports", json={"kind": "excel"})
    assert not_ready.status_code == 409
    assert "survey_date" in not_ready.json()["detail"]["missing"]

    client.patch(
        f"/api/reports/{report['id']}", json={"survey_date": "2026-10-01", "buried_years": "5"}
    )
    client.patch(
        f"/api/reports/{report['id']}/pipe-information",
        json={"length_m": 10, "total_drive_distance_m": 10},
    )
    excel = client.post(f"/api/reports/{report['id']}/exports", json={"kind": "excel"}).json()
    job = client.get(f"/api/jobs/{excel['id']}").json()
    assert job["status"] == "succeeded", job
    download = client.get(_path_and_query(job["download_url"]))
    assert "attachment" in download.headers["content-disposition"]
    workbook = load_workbook(io.BytesIO(download.content))
    assert workbook.sheetnames == ["기본정보", "관로조사정보", "결함목록"]
    memo_cells = [
        cell
        for row in workbook["결함목록"].iter_rows()
        for cell in row
        if isinstance(cell.value, str) and "HYPERLINK" in cell.value
    ]
    assert memo_cells and all(cell.data_type == "s" for cell in memo_cells)

    pdf = client.post(f"/api/reports/{report['id']}/exports", json={"kind": "pdf"}).json()
    pdf_job = client.get(f"/api/jobs/{pdf['id']}").json()
    assert pdf_job["status"] == "succeeded", pdf_job
    assert client.get(_path_and_query(pdf_job["download_url"])).content.startswith(b"%PDF")


def test_comparison_requires_matching_reports(client: TestClient, report: dict) -> None:
    client.patch(
        f"/api/reports/{report['id']}", json={"survey_date": "2026-10-01", "buried_years": "5"}
    )
    client.patch(
        f"/api/reports/{report['id']}/pipe-information",
        json={"length_m": 10, "total_drive_distance_m": 10},
    )
    after = client.post(f"/api/report-groups/{report['report_group_id']}/duplicate", json={}).json()
    client.patch(f"/api/reports/{after['id']}", json={"inspector": "다른 사람"})
    response = client.post(
        f"/api/reports/{report['id']}/exports",
        json={"kind": "pdf", "report_type": "comparison", "after_report_id": after["id"]},
    )
    assert response.status_code == 409
    assert "report.inspector" in response.json()["detail"]["fields"]


def test_stop_analysis_job(client: TestClient, report: dict, tmp_path: Path) -> None:
    upload(client, report["id"], make_video(tmp_path / "v.avi"))
    no_roi = client.post(f"/api/reports/{report['id']}/stop-analysis", json={})
    assert no_roi.status_code == 409
    client.patch(
        f"/api/reports/{report['id']}/video",
        json={"depth_roi": {"x": 10, "y": 10, "w": 100, "h": 40}},
    )
    started = client.post(f"/api/reports/{report['id']}/stop-analysis", json={})
    assert started.status_code == 202
    job = client.get(f"/api/jobs/{started.json()['id']}").json()
    assert job["status"] == "succeeded", job
    assert isinstance(job["result"]["segments"], list)


def test_depth_roi_must_fit_the_video(client: TestClient, report: dict, tmp_path: Path) -> None:
    upload(client, report["id"], make_video(tmp_path / "v.avi"))
    response = client.patch(
        f"/api/reports/{report['id']}/video",
        json={"depth_roi": {"x": 300, "y": 0, "w": 100, "h": 40}},
    )
    assert response.status_code == 422


def test_quota_is_enforced(app_engine, owner_engine: Engine, clock: Clock, tmp_path: Path) -> None:
    settings = make_settings(organization_storage_quota_bytes=1000)
    with make_client(app_engine, clock, settings) as small:
        login(small, create_user(owner_engine))
        small.headers.update(csrf_headers(small))
        project = small.post(
            "/api/projects", json={"project_name": "P", "visibility": "org"}
        ).json()
        business = small.post(
            f"/api/projects/{project['id']}/businesses",
            json={"business_code": "B1", "business_name": "사업"},
        ).json()
        report = small.post(
            f"/api/businesses/{business['id']}/reports",
            json={"report_number": "R", "pipe_number": "P"},
        ).json()
        response = small.post(
            f"/api/reports/{report['id']}/video/uploads",
            json={"filename": "v.avi", "size_bytes": 1001},
        )
        assert response.status_code == 507
