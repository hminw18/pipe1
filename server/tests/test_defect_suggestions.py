"""AI defect suggestions: the detection job stores the GPU server's tracks as suggestions,
an inspector confirms them by recording a defect or rejects them."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from pipe1_server.detection import (
    JOB_TIMEOUT_SECONDS,
    DetectorError,
    HttpDefectDetector,
    wait_for_result,
)
from tests.api_support import Clock, create_user, csrf_headers, login, make_client, make_settings
from tests.test_media_pipeline import make_video, upload

# The 6-second test video; times past its end are clamped.
RESULT = {
    "model_version": "test-1",
    "tracks": [
        {
            "code": "CL",
            "confidence": 0.9,
            "start_s": 1.0,
            "end_s": 2.0,
            "boxes": [
                {"t": 1.0, "box": [0.1, 0.1, 0.3, 0.3], "score": 0.8},
                {"t": 1.6, "box": [0.2, 0.2, 0.4, 0.4], "score": 0.9},
            ],
        },
        {"code": "RIm", "confidence": 0.6, "start_s": 4.0, "end_s": 9.0, "best_time_s": 8.0},
        {"code": "ZZ", "confidence": 0.7, "start_s": 3.0, "end_s": 3.5},
    ],
}


@pytest.fixture
def detector_file(tmp_path: Path) -> Path:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(RESULT))
    return path


@pytest.fixture
def ai(app_engine, owner_engine: Engine, clock: Clock, detector_file: Path):
    settings = make_settings(defect_detector_sample_file=str(detector_file))
    with make_client(app_engine, clock, settings) as client:
        login(client, create_user(owner_engine))
        client.headers.update(csrf_headers(client))
        yield client


def _report(client: TestClient) -> dict:
    project = client.post("/api/projects", json={"project_name": "P", "visibility": "org"}).json()
    business = client.post(
        f"/api/projects/{project['id']}/businesses",
        json={"business_code": "B1", "business_name": "사업"},
    ).json()
    return client.post(
        f"/api/businesses/{business['id']}/reports",
        json={"report_number": "R001", "pipe_number": "P-001"},
    ).json()


def _detect(client: TestClient, report_id: str) -> dict:
    started = client.post(f"/api/reports/{report_id}/defect-detection", json={})
    assert started.status_code == 202, started.text
    return client.get(f"/api/jobs/{started.json()['id']}").json()


def test_detection_is_off_without_a_detector(
    client: TestClient, owner_engine: Engine, tmp_path: Path
) -> None:
    login(client, create_user(owner_engine))
    client.headers.update(csrf_headers(client))
    report = _report(client)
    upload(client, report["id"], make_video(tmp_path / "v.avi"))

    assert client.post(f"/api/reports/{report['id']}/defect-detection", json={}).status_code == 409
    listing = client.get(f"/api/reports/{report['id']}/suggestions").json()
    assert listing == {"available": False, "suggestions": []}


def test_tracks_become_suggestions_that_defects_confirm(ai: TestClient, tmp_path: Path) -> None:
    report = _report(ai)
    upload(ai, report["id"], make_video(tmp_path / "v.avi"))

    job = _detect(ai, report["id"])
    assert job["status"] == "succeeded", job
    assert job["result"] == {
        "model_version": "test-1",
        "suggestions": 2,
        "skipped_codes": {"ZZ": 1},
    }

    listing = ai.get(f"/api/reports/{report['id']}/suggestions").json()
    assert listing["available"] is True
    crack, roots = listing["suggestions"]
    assert (crack["item_category"], crack["defect_item"]) == ("관로", "균열(길이)")
    assert (crack["start_ms"], crack["best_ms"], crack["end_ms"]) == (1000, 1500, 2000)
    assert crack["best_box"] == [0.2, 0.2, 0.4, 0.4]  # the sample nearest the best time
    assert [sample["t_ms"] for sample in crack["boxes"]] == [1000, 1600]
    assert (roots["item_category"], roots["defect_item"]) == ("맨홀", "뿌리침입")
    assert (roots["best_ms"], roots["end_ms"]) == (6000, 6000)
    assert roots["best_box"] is None

    rejected = ai.patch(f"/api/suggestions/{roots['id']}", json={"status": "rejected"})
    assert rejected.json()["status"] == "rejected"

    defect = ai.post(
        f"/api/reports/{report['id']}/defects",
        json={
            "timestamp_ms": crack["best_ms"],
            "drive_direction": "순주행",
            "distance_m": 3.2,
            "item_category": "관로",
            "defect_item": "균열(원주)",  # the inspector may correct the model
            "grade": "중",
            "suggestion_id": crack["id"],
        },
    ).json()
    crack, _ = ai.get(f"/api/reports/{report['id']}/suggestions").json()["suggestions"]
    assert (crack["status"], crack["defect_id"]) == ("confirmed", defect["id"])
    assert (
        ai.patch(f"/api/suggestions/{crack['id']}", json={"status": "rejected"}).status_code == 409
    )

    # A new version carries the suggestions, linked to its own copies of the defects.
    version = ai.post(f"/api/report-groups/{report['report_group_id']}/versions").json()
    copied = ai.get(f"/api/reports/{version['id']}/suggestions").json()["suggestions"]
    copied_defect = ai.get(f"/api/reports/{version['id']}/defects").json()[0]
    assert [s["status"] for s in copied] == ["confirmed", "rejected"]
    assert copied[0]["defect_id"] == copied_defect["id"]

    ai.delete(f"/api/defects/{defect['id']}")
    crack, _ = ai.get(f"/api/reports/{report['id']}/suggestions").json()["suggestions"]
    assert (crack["status"], crack["defect_id"]) == ("pending", None)

    # Another run replaces the suggestions; replacing the video removes them.
    assert _detect(ai, report["id"])["status"] == "succeeded"
    statuses = [
        s["status"]
        for s in ai.get(f"/api/reports/{report['id']}/suggestions").json()["suggestions"]
    ]
    assert statuses == ["pending", "pending"]
    upload(ai, report["id"], make_video(tmp_path / "w.avi"))
    assert ai.get(f"/api/reports/{report['id']}/suggestions").json()["suggestions"] == []


def test_suggestion_from_another_report_is_refused(ai: TestClient, tmp_path: Path) -> None:
    first = _report(ai)
    second = ai.post(
        f"/api/businesses/{first['business_id']}/reports",
        json={"report_number": "R002", "pipe_number": "P-002"},
    ).json()
    upload(ai, first["id"], make_video(tmp_path / "v.avi"))
    _detect(ai, first["id"])
    suggestion = ai.get(f"/api/reports/{first['id']}/suggestions").json()["suggestions"][0]

    response = ai.post(
        f"/api/reports/{second['id']}/defects",
        json={
            "timestamp_ms": 1000,
            "drive_direction": "순주행",
            "distance_m": 1.0,
            "suggestion_id": suggestion["id"],
        },
    )
    assert response.status_code == 422


def test_invalid_detector_output_fails_the_job(
    ai: TestClient, detector_file: Path, tmp_path: Path
) -> None:
    report = _report(ai)
    upload(ai, report["id"], make_video(tmp_path / "v.avi"))
    detector_file.write_text(
        json.dumps(
            {
                "model_version": "bad",
                "tracks": [{"code": "CL", "confidence": 2.0, "start_s": 0, "end_s": 1}],
            }
        )
    )

    job = _detect(ai, report["id"])
    assert job["status"] == "failed"
    assert "AI 분석 서버" in job["error"]
    assert ai.get(f"/api/reports/{report['id']}/suggestions").json()["suggestions"] == []


def test_http_detector_follows_the_contract() -> None:
    """What the GPU server receives, and which failures end the job: a short outage is
    ridden out, an error status is not, and the deadline counts from submission."""
    polls = iter(["running", "down", "succeeded"])

    def gpu_server(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == "Bearer secret"
        if request.method == "POST":
            assert request.url.raw_path == b"/gpu/v1/jobs"
            assert json.loads(request.content) == {"video_url": "https://video", "sample_fps": 5}
            return httpx2.Response(202, json={"job_id": "a/b"})
        assert request.url.raw_path == b"/gpu/v1/jobs/a%2Fb"
        state = next(polls)
        if state == "down":
            raise httpx2.ConnectError("refused", request=request)
        if state == "running":
            return httpx2.Response(200, json={"status": "running", "progress": 40})
        return httpx2.Response(200, json={"status": "succeeded", "result": RESULT})

    def detector(handler) -> HttpDefectDetector:
        client = httpx2.Client(transport=httpx2.MockTransport(handler))
        return HttpDefectDetector("http://gpu/gpu/", "secret", client=client)

    now = [1000.0]
    seen: list[int] = []
    gpu = detector(gpu_server)
    job_id = gpu.submit("https://video")
    result = wait_for_result(
        gpu,
        job_id,
        now[0],
        lambda value, _message: seen.append(value),
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert (result.model_version, len(result.tracks), seen) == ("test-1", 3, [40])

    with pytest.raises(httpx2.HTTPStatusError):
        detector(lambda request: httpx2.Response(404)).poll("gone")
    with pytest.raises(DetectorError, match="timed out"):
        wait_for_result(
            gpu, job_id, now[0] - JOB_TIMEOUT_SECONDS, lambda *_: None, clock=lambda: now[0]
        )
