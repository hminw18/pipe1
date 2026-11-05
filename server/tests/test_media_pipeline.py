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
from sqlalchemy import Engine, text

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


def test_stop_analysis_job(
    client: TestClient, report: dict, owner_engine: Engine, tmp_path: Path
) -> None:
    """Analysis runs on the processed video; replacing the video ends analyses still running
    on the old one and hides its results."""
    from sqlalchemy.orm import Session

    from pipe1_server.models.inspection import MediaJob

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
    assert job["result"]["travel_start_s"] is None  # the test video has no depth counter

    with Session(owner_engine) as db, db.begin():
        stuck = MediaJob(
            project_id=report["project_id"],
            report_id=report["id"],
            kind="stop_analysis",
            status="running",
            created_by=db.execute(
                text("SELECT id FROM users ORDER BY created_at DESC LIMIT 1")
            ).scalar_one(),
        )
        db.add(stuck)
        db.flush()
        stuck_id = str(stuck.id)
    upload(client, report["id"], make_video(tmp_path / "w.avi"))
    assert client.get(f"/api/jobs/{stuck_id}").json()["status"] == "failed"
    assert client.get(f"/api/reports/{report['id']}/jobs?kind=stop_analysis").json() == []
    again = client.post(f"/api/reports/{report['id']}/stop-analysis", json={})
    assert again.status_code == 202
    listed = client.get(f"/api/reports/{report['id']}/jobs?kind=stop_analysis").json()
    assert [j["id"] for j in listed] == [again.json()["id"]]


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


# --- Fast path for browser-playable sources, browser captures, job recovery -------------


def make_h264_video(tmp_path: Path) -> Path:
    """H.264 4:2:0 in an MP4 with the index at the end, as CCTV recorders write it."""
    import subprocess

    target = tmp_path / "recorder.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(make_video(tmp_path / "src.avi"))]
        + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)],
        check=True,
    )
    return target


def test_browser_playable_mp4_is_played_as_uploaded(
    client: TestClient, report: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipe1_server import media

    def no_rewrite(*_: object) -> None:
        raise AssertionError("a browser-playable MP4 must not be rewritten")

    monkeypatch.setattr(media, "transcode_for_browser", no_rewrite)
    monkeypatch.setattr(media, "remux_for_browser", no_rewrite)
    source = make_h264_video(tmp_path)
    video = upload(client, report["id"], source)
    assert video["transcode_status"] == "ready", video
    assert client.get(_path_and_query(video["playback_url"])).content == source.read_bytes()


def _png(width: int = 320, height: int = 240, value: int = 77) -> bytes:
    ok, encoded = cv2.imencode(".png", np.full((height, width, 3), value, dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def _start_upload(client: TestClient, report_id: str, data: bytes) -> dict:
    return client.post(
        f"/api/reports/{report_id}/video/uploads",
        json={"filename": "v.avi", "size_bytes": len(data)},
    ).json()


def test_defects_can_be_captured_in_the_browser_while_uploading(
    client: TestClient, report: dict, tmp_path: Path
) -> None:
    data = make_video(tmp_path / "v.avi").read_bytes()
    session = _start_upload(client, report["id"], data)
    video = client.get(f"/api/reports/{report['id']}/video").json()
    assert video["transcode_status"] == "uploading" and video["playback_url"] is None

    base = {"timestamp_ms": 1500, "drive_direction": "순주행", "distance_m": 3.0}
    server_side = client.post(f"/api/reports/{report['id']}/defects", json=base)
    assert server_side.status_code == 409  # nothing to extract a frame from yet
    captured = client.post(
        f"/api/reports/{report['id']}/defects", json={**base, "frame_png_base64": _b64(_png())}
    )
    assert captured.status_code == 201, captured.text
    image = client.get(_path_and_query(captured.json()["image_url"])).content
    frame = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert frame.shape == (240, 320) and int(np.median(frame)) == 77

    client.put(_path_and_query(session["url"]), content=data, headers=session["headers"])
    done = client.post(f"/api/video-uploads/{session['upload_id']}/complete", json={})
    assert done.status_code == 200 and done.json()["id"] == video["id"]
    assert client.get(f"/api/reports/{report['id']}/video").json()["transcode_status"] == "ready"
    assert len(client.get(f"/api/reports/{report['id']}/defects").json()) == 1


@pytest.mark.parametrize(
    "frame",
    [
        b"GIF89a not a png",
        _png()[:40],  # truncated
        # A valid header claiming 100000 px wide is refused before decoding.
        _png()[:16] + (100000).to_bytes(4, "big") + _png()[20:],
    ],
)
def test_unusable_browser_frames_are_rejected(
    client: TestClient, report: dict, tmp_path: Path, frame: bytes
) -> None:
    _start_upload(client, report["id"], b"x" * 10)
    response = client.post(
        f"/api/reports/{report['id']}/defects",
        json={
            "timestamp_ms": 0,
            "drive_direction": "순주행",
            "distance_m": 1.0,
            "frame_png_base64": _b64(frame),
        },
    )
    assert response.status_code == 422
    assert client.get(f"/api/reports/{report['id']}/defects").json() == []


def test_interrupted_jobs_are_resumed_at_startup(
    app_engine: Engine, owner_engine: Engine, report: dict
) -> None:
    import uuid

    from sqlalchemy import update
    from sqlalchemy.orm import Session, sessionmaker

    from pipe1_server.jobs import BackgroundJobQueue
    from pipe1_server.models.inspection import MediaJob

    running, queued, done = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with Session(owner_engine) as db, db.begin():
        user_id = db.execute(
            text("SELECT id FROM users ORDER BY created_at DESC LIMIT 1")
        ).scalar_one()
        jobs = [
            MediaJob(
                id=job_id,
                project_id=report["project_id"],
                report_id=report["id"],
                kind="export_excel",
                status=status,
                created_by=user_id,
            )
            for job_id, status in ((running, "running"), (queued, "queued"), (done, "succeeded"))
        ]
        db.add_all(jobs)
    ran: list[uuid.UUID] = []

    class Runner:
        def run(self, job_id: uuid.UUID, _: uuid.UUID) -> None:
            ran.append(job_id)

    queue = BackgroundJobQueue(Runner())  # type: ignore[arg-type]
    try:
        queue.resume_unfinished(sessionmaker(app_engine))
        queue.join()
        assert {running, queued} <= set(ran)
        assert done not in ran
    finally:
        with Session(owner_engine) as db, db.begin():
            db.execute(
                update(MediaJob).where(MediaJob.id.in_([running, queued])).values(status="failed")
            )
