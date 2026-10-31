"""Background jobs (video processing, stop analysis, exports) and synchronous media calls.

Jobs run in the worker in production (Cloud Tasks → /internal/tasks/job). A task carries the
job id and the creating user's id. The worker loads that user, sets the user's *current*
access context, and loads the job through row-level security, so authorization is re-checked
when the job runs (docs/Web_Auth_Spec.md §10.7): a user who lost access, was deactivated, or
whose organization was disabled no longer runs anything.

Users see generic error messages (or a UserFacingError's message); details go to the log.
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from pipe1_server.db import set_access_context
from pipe1_server.models.auth import Organization, User
from pipe1_server.models.inspection import (
    InspectionProject,
    MediaJob,
    Report,
    ReportVideo,
)
from pipe1_server.services import exports
from pipe1_server.storage import (
    LocalObjectStore,
    ObjectStore,
    report_group_prefix,
    report_prefix,
)

logger = logging.getLogger(__name__)

WORKER_SOURCE_TTL = timedelta(hours=6)
PROGRESS_WRITE_INTERVAL = 1.0
GENERIC_FAILURE = "처리 중 오류가 발생했습니다. 다시 시도하거나 관리자에게 문의하세요."
INVALID_VIDEO = "지원하지 않거나 손상된 영상입니다. avi, mkv, mov, mp4 영상을 올려 주세요."


class UserFacingError(RuntimeError):
    """A failure whose message is safe and useful to show to the user."""


# --- Queues -------------------------------------------------------------------------


class JobQueue(Protocol):
    def enqueue(self, job_id: uuid.UUID, user_id: uuid.UUID) -> None: ...


class InMemoryJobQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[uuid.UUID, uuid.UUID]] = []

    def enqueue(self, job_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.jobs.append((job_id, user_id))


class InlineJobQueue:
    """Tests: run right away, in the caller's thread (after the commit)."""

    def __init__(self, runner: JobRunner) -> None:
        self._runner = runner

    def enqueue(self, job_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self._runner.run(job_id, user_id)


class BackgroundJobQueue:
    """Single-host deployments (the dev VM): worker threads in the API process, so a long
    video job never holds up a request. Jobs left queued or running by a restart are picked
    up again at startup (resume_unfinished); production relies on Cloud Tasks retries."""

    def __init__(self, runner: JobRunner, *, threads: int = 2) -> None:
        self._runner = runner
        self._queue: queue.Queue[tuple[uuid.UUID, uuid.UUID]] = queue.Queue()
        for index in range(threads):
            threading.Thread(target=self._run, name=f"pipe1-jobs-{index}", daemon=True).start()

    def enqueue(self, job_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self._queue.put((job_id, user_id))

    def join(self) -> None:
        """Wait until every queued job has finished (tests)."""
        self._queue.join()

    def resume_unfinished(self, session_factory: sessionmaker[Session]) -> int:
        with session_factory.begin() as db:
            rows = db.execute(text("SELECT id, created_by FROM app_unfinished_media_jobs()")).all()
        for job_id, user_id in rows:
            logger.warning("resuming job %s interrupted by a restart", job_id)
            self.enqueue(job_id, user_id)
        return len(rows)

    def _run(self) -> None:
        while True:
            job_id, user_id = self._queue.get()
            try:
                self._runner.run(job_id, user_id)
            except Exception:  # noqa: BLE001 - keep the thread alive; run() records failures
                logger.exception("job %s crashed", job_id)
            finally:
                self._queue.task_done()


class CloudTasksJobQueue:
    def __init__(self, queue_name: str, worker_url: str, service_account: str) -> None:
        from google.cloud import tasks_v2

        self._client = tasks_v2.CloudTasksClient()
        self._queue = queue_name
        self._audience = worker_url.rstrip("/")
        self._url = self._audience + "/internal/tasks/job"
        self._service_account = service_account

    def enqueue(self, job_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self._client.create_task(
            request={
                "parent": self._queue,
                "task": {
                    "http_request": {
                        "http_method": "POST",
                        "url": self._url,
                        "headers": {"Content-Type": "application/json"},
                        "body": json.dumps(
                            {"job_id": str(job_id), "user_id": str(user_id)}
                        ).encode(),
                        "oidc_token": {
                            "service_account_email": self._service_account,
                            "audience": self._audience,
                        },
                    },
                    # Multi-GB transcodes need the longest dispatch deadline Cloud Tasks allows.
                    "dispatch_deadline": {"seconds": 1800},
                },
            }
        )


# --- Media ----------------------------------------------------------------------------


def media_source(store: ObjectStore, key: str) -> str:
    """Something ffmpeg/OpenCV can open: a local path (dev) or a signed HTTPS URL."""
    if isinstance(store, LocalObjectStore):
        return str(store.path_for(key))
    return store.signed_download_url(key, WORKER_SOURCE_TTL)


class MediaClient(Protocol):
    def capture(
        self, object_key: str, timestamp_ms: int, roi: tuple[int, int, int, int] | None
    ) -> tuple[bytes, float | None]: ...

    def from_image(
        self, data: bytes, roi: tuple[int, int, int, int] | None
    ) -> tuple[bytes, float | None]:
        """Validate a frame captured in the browser; raises media.MediaError if unusable."""
        ...


class LocalMediaClient:
    """Development and tests: parse in-process. Production uses WorkerMediaClient so
    untrusted files are parsed only inside the isolated worker."""

    def __init__(self, store: ObjectStore) -> None:
        self._store = store

    def capture(self, object_key, timestamp_ms, roi):
        from pipe1_server import media

        png = media.extract_frame_png(media_source(self._store, object_key), timestamp_ms)
        return png, media.read_distance(png, roi) if roi else None

    def from_image(self, data, roi):
        from pipe1_server import media

        png = media.normalize_frame_png(data)
        return png, media.read_distance(png, roi) if roi else None


class WorkerMediaClient:
    """Calls the worker with a Google-signed ID token for the API's service account."""

    def __init__(self, worker_url: str) -> None:
        self._url = worker_url.rstrip("/")

    def capture(self, object_key, timestamp_ms, roi):
        return self._post(
            "capture", {"object_key": object_key, "timestamp_ms": timestamp_ms, "roi": roi}
        )

    def from_image(self, data, roi):
        from pipe1_server import media

        try:
            return self._post(
                "frame", {"png_base64": base64.b64encode(data).decode("ascii"), "roi": roi}
            )
        except _Rejected as error:
            raise media.MediaError("frame rejected by the worker") from error

    def _post(self, endpoint: str, body: dict[str, Any]) -> tuple[bytes, float | None]:
        import httpx2
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        token = id_token.fetch_id_token(Request(), self._url)
        response = httpx2.post(
            f"{self._url}/internal/media/{endpoint}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=90.0,
        )
        if response.status_code == 422:
            raise _Rejected(endpoint)
        response.raise_for_status()
        payload = response.json()
        return base64.b64decode(payload["png_base64"]), payload.get("distance")


class _Rejected(Exception):
    pass


# --- Runner -------------------------------------------------------------------------


def _active_user(session_factory: sessionmaker[Session], user_id: uuid.UUID) -> User | None:
    with session_factory.begin() as db:
        user = db.get(User, user_id)
        if user is None or user.status != "active" or user.organization_id is None:
            return None
        organization = db.get(Organization, user.organization_id)
        if organization is None or organization.status != "active":
            return None
        db.expunge(user)
        return user


class JobRunner:
    def __init__(self, session_factory: sessionmaker[Session], store: ObjectStore) -> None:
        self._sessions = session_factory
        self._store = store

    @contextmanager
    def session(self, user: User) -> Iterator[Session]:
        with self._sessions.begin() as db:
            set_access_context(
                db, organization_id=user.organization_id, user_id=user.id, role=user.role
            )
            yield db

    def run(self, job_id: uuid.UUID, user_id: uuid.UUID) -> None:
        user = _active_user(self._sessions, user_id)
        if user is None:
            logger.warning("job %s skipped: user %s inactive", job_id, user_id)
            return
        with self.session(user) as db:
            job = db.get(MediaJob, job_id)
            if job is None or job.created_by != user.id or job.status not in ("queued", "running"):
                logger.warning(
                    "job %s skipped: not visible to its creator or already finished", job_id
                )
                return
            job.status, job.progress, job.message, job.error = "running", 0, None, None
            kind, parameters, report_id = job.kind, dict(job.parameters), job.report_id

        handler: Callable[..., dict[str, Any]] = getattr(self, f"_run_{kind}")
        try:
            result = handler(user, job_id, report_id, parameters)
        except Exception as error:  # noqa: BLE001 - every failure is recorded on the job
            logger.exception("job %s (%s) failed", job_id, kind)
            message = str(error) if isinstance(error, UserFacingError) else GENERIC_FAILURE
            with self.session(user) as db:
                job = db.get(MediaJob, job_id)
                if job is not None:
                    job.status, job.error = "failed", message
            return
        with self.session(user) as db:
            job = db.get(MediaJob, job_id)
            if job is not None:
                job.status, job.progress, job.result = "succeeded", 100, result
                job.message = None

    def _progress(self, user: User, job_id: uuid.UUID) -> Callable[[int, str], None]:
        last = [0.0]

        def report(value: int, message: str) -> None:
            now = time.monotonic()
            if now - last[0] < PROGRESS_WRITE_INTERVAL and value < 100:
                return
            last[0] = now
            with self.session(user) as db:
                job = db.get(MediaJob, job_id)
                if job is not None:
                    job.progress, job.message = max(0, min(99, int(value))), message[:500]

        return report

    def _report_project(
        self, db: Session, report_id: uuid.UUID
    ) -> tuple[Report, InspectionProject]:
        report = db.get(Report, report_id)
        project = db.get(InspectionProject, report.project_id) if report else None
        if report is None or project is None:
            raise UserFacingError("보고서를 찾을 수 없습니다.")
        return report, project

    # -- process_video -----------------------------------------------------------------

    def _run_process_video(self, user, job_id, report_id, parameters) -> dict[str, Any]:
        from pipe1_server import media

        with self.session(user) as db:
            report, project = self._report_project(db, report_id)
            video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report_id))
            if video is None:
                raise UserFacingError("영상이 없습니다.")
            video.transcode_status, video.processing_error = "processing", None
            original_key = video.original_object_key
            group_prefix = report_group_prefix(
                project.organization_id, project.id, report.report_group_id
            )
        progress = self._progress(user, job_id)
        with tempfile.TemporaryDirectory(prefix="pipe1-video-") as tmp:
            directory = Path(tmp)
            source = media_source(self._store, original_key)
            progress(5, "영상 확인")
            try:
                probe = media.probe(source)
            except media.MediaError as error:
                logger.warning("video %s rejected: %s", original_key, error)
                with self.session(user) as db:
                    video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report_id))
                    if video is not None:
                        video.transcode_status, video.processing_error = "failed", INVALID_VIDEO
                    still_used = db.scalar(
                        select(ReportVideo.id).where(
                            ReportVideo.original_object_key == original_key,
                            ReportVideo.report_id != report_id,
                        )
                    )
                if still_used is None:
                    self._store.delete(original_key)
                raise UserFacingError(INVALID_VIDEO) from error
            playback = directory / "playback.mp4"
            self._prepare_playback(source, playback, probe, progress)
            playback_key = f"{group_prefix}video/{uuid.uuid4()}/playback.mp4"
            self._store.upload_from(playback, playback_key, "video/mp4")
        with self.session(user) as db:
            video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report_id))
            if video is None or video.original_object_key != original_key:
                # Replaced while processing; discard this output.
                self._store.delete(playback_key)
                raise UserFacingError("처리 중 영상이 교체되었습니다.")
            video.playback_object_key = playback_key
            video.duration_seconds = probe.duration_seconds
            video.fps = probe.fps
            video.width, video.height = probe.width, probe.height
            video.transcode_status, video.processing_error = "ready", None
        return {
            "duration_seconds": probe.duration_seconds,
            "width": probe.width,
            "height": probe.height,
        }

    @staticmethod
    def _prepare_playback(source, playback: Path, probe, progress) -> None:
        from pipe1_server import media

        if probe.browser_playable:
            progress(15, "재생용 영상 준비")
            try:
                media.remux_for_browser(source, playback)
                return
            except media.MediaError as error:
                logger.warning("remux failed, re-encoding instead: %s", error)
        progress(15, "재생용 영상 변환")
        media.transcode_for_browser(source, playback)

    # -- stop_analysis -------------------------------------------------------------------

    def _run_stop_analysis(self, user, job_id, report_id, parameters) -> dict[str, Any]:
        from pipe1_core.stop_detection.pipeline import run_stop_analysis

        with self.session(user) as db:
            video = db.scalar(select(ReportVideo).where(ReportVideo.report_id == report_id))
            if video is None or video.transcode_status != "ready":
                raise UserFacingError("영상 처리가 끝난 뒤 분석할 수 있습니다.")
            if video.depth_roi_x is None:
                raise UserFacingError("먼저 깊이 영역을 저장하세요.")
            roi = (video.depth_roi_x, video.depth_roi_y, video.depth_roi_w, video.depth_roi_h)
            # The playback copy: the frames and timestamps the player and captures use, and
            # keyframes at most 2 s apart when it was re-encoded.
            playback_key = video.playback_object_key
        progress = self._progress(user, job_id)
        with tempfile.TemporaryDirectory(prefix="pipe1-analysis-") as tmp:
            if isinstance(self._store, LocalObjectStore):
                path = self._store.path_for(playback_key)
            else:
                # Stop analysis seeks repeatedly from several threads; a local copy is far
                # faster and more reliable than streaming over HTTPS.
                path = Path(tmp) / "playback.mp4"
                progress(1, "영상 내려받는 중")
                self._store.download_to(playback_key, path)
            result = run_stop_analysis(str(path), roi, progress=progress)
        return {"segments": result.segments, "benchmark": result.benchmark}

    # -- exports -------------------------------------------------------------------------

    def _store_export(
        self, project, report_id, job_id, path: Path, content_type: str
    ) -> dict[str, Any]:
        prefix = report_prefix(project.organization_id, project.id, report_id)
        key = f"{prefix}exports/{job_id}/{path.name}"
        self._store.upload_from(path, key, content_type)
        return {"object_key": key, "filename": path.name}

    def _run_export_excel(self, user, job_id, report_id, parameters) -> dict[str, Any]:
        with self.session(user) as db:
            _, project = self._report_project(db, report_id)
            data = exports.load_report_data(db, report_id)
        with tempfile.TemporaryDirectory(prefix="pipe1-export-") as tmp:
            directory = Path(tmp)
            images = exports.download_captures(self._store, data.defects, directory / "captures")
            output = exports.render_excel(data, images, directory / exports.excel_filename(data))
            return self._store_export(
                project,
                report_id,
                job_id,
                output,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    def _run_export_pdf(self, user, job_id, report_id, parameters) -> dict[str, Any]:
        report_type = parameters.get("report_type", "inspection")
        after_id = parameters.get("after_report_id")
        with self.session(user) as db:
            _, project = self._report_project(db, report_id)
            before = exports.load_report_data(db, report_id)
            after = exports.load_report_data(db, uuid.UUID(after_id)) if after_id else None
        if after is not None and exports.comparison_mismatches(before, after):
            raise UserFacingError("보수후 보고서의 기본 정보가 보수전 보고서와 다릅니다.")
        with tempfile.TemporaryDirectory(prefix="pipe1-export-") as tmp:
            directory = Path(tmp)
            before_images = exports.download_captures(
                self._store, before.defects, directory / "before"
            )
            after_pair = (
                (after, exports.download_captures(self._store, after.defects, directory / "after"))
                if after
                else None
            )
            output = exports.render_pdf(
                before, before_images, after_pair, report_type, directory / "out"
            )
            return self._store_export(project, report_id, job_id, output, "application/pdf")

    def _run_export_business(self, user, job_id, report_id, parameters) -> dict[str, Any]:
        kind = parameters["workbook"]
        report_ids = [uuid.UUID(value) for value in parameters["report_ids"]]
        with self.session(user) as db:
            _, project = self._report_project(db, report_id)
            loaded = [exports.load_report_data(db, rid) for rid in report_ids]
            business_code = loaded[0].context["business_code"]
        with tempfile.TemporaryDirectory(prefix="pipe1-export-") as tmp:
            directory = Path(tmp)
            reports = [
                (data, exports.download_captures(self._store, data.defects, directory / str(i)))
                for i, data in enumerate(loaded)
            ]
            name = "InternalDefects" if kind == "internal" else "DefectAggregate"
            output = exports.render_business_workbook(
                kind,
                reports,
                directory / f"{business_code}_{name}.xlsx",
                include_photos=bool(parameters.get("include_photos")),
            )
            return self._store_export(
                project,
                report_id,
                job_id,
                output,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
