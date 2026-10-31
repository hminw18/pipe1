"""Background worker service (Cloud Run, ingress internal). Invoked only by Cloud Tasks.

Every request must carry a Google-signed OIDC token for the configured Cloud Tasks
service account, with this service's URL as audience (docs/Web_Auth_Spec.md §10.7).
"""

from __future__ import annotations

import base64
import logging
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from pipe1_server.db import get_session_factory
from pipe1_server.detection import build_detector
from pipe1_server.email.delivery import (
    EmailMessage,
    EmailSender,
    PermanentEmailError,
    build_sender,
)
from pipe1_server.email.templates import mask_email
from pipe1_server.jobs import JobRunner, media_source
from pipe1_server.media import MAX_FRAME_BYTES
from pipe1_server.settings import Settings, get_settings
from pipe1_server.storage import build_store

logger = logging.getLogger(__name__)

TokenVerifier = Callable[[str, str], dict[str, Any]]


def google_token_verifier(token: str, audience: str) -> dict[str, Any]:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, google_requests.Request(), audience=audience)


def get_token_verifier() -> TokenVerifier:
    return google_token_verifier


def get_sender(settings: Annotated[Settings, Depends(get_settings)]) -> EmailSender:
    return build_sender(settings)


def _verify_caller(
    request: Request, settings: Settings, verify: TokenVerifier, expected_account: str | None
) -> None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    if not (settings.worker_url and expected_account):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "worker not configured")
    try:
        claims = verify(token, settings.worker_url.rstrip("/"))
    except ValueError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED) from error
    if claims.get("email") != expected_account or not claims.get("email_verified"):
        raise HTTPException(status.HTTP_403_FORBIDDEN)


def require_cloud_tasks(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    verify: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> None:
    _verify_caller(request, settings, verify, settings.tasks_service_account)


def require_api_service(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    verify: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> None:
    _verify_caller(request, settings, verify, settings.api_service_account)


def get_runner(
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobRunner:
    return JobRunner(session_factory, build_store(settings), build_detector(settings))


class JobTask(BaseModel):
    job_id: uuid.UUID
    user_id: uuid.UUID


class CaptureRequest(BaseModel):
    object_key: str = Field(max_length=1024)
    timestamp_ms: int = Field(ge=0)
    roi: tuple[int, int, int, int] | None = None


class FrameRequest(BaseModel):
    # base64 inflates by 4/3.
    png_base64: str = Field(max_length=MAX_FRAME_BYTES * 4 // 3 + 4)
    roi: tuple[int, int, int, int] | None = None


class CaptureResponse(BaseModel):
    png_base64: str
    distance: float | None


def create_worker_app() -> FastAPI:
    app = FastAPI(title="Pipe1 worker", docs_url=None, redoc_url=None, openapi_url=None)

    @app.post(
        "/internal/tasks/email",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_cloud_tasks)],
    )
    async def send_email(
        request: Request, sender: Annotated[EmailSender, Depends(get_sender)]
    ) -> None:
        try:
            message = EmailMessage.from_json(await request.body())
        except (ValueError, KeyError) as error:
            # Malformed tasks are not retried.
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT) from error
        # Transient provider errors propagate as 500 so Cloud Tasks retries with backoff; a
        # message the provider rejects outright is dropped instead of retried forever.
        try:
            sender.send(message)
        except PermanentEmailError as error:
            recipient = mask_email(message.to)
            logger.error("email %s to %s not sent: %s", message.template, recipient, error)

    @app.post(
        "/internal/tasks/job",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_cloud_tasks)],
    )
    def run_job(task: JobTask, runner: Annotated[JobRunner, Depends(get_runner)]) -> None:
        # Job failures are recorded on the job itself; the task succeeds so it is not
        # retried into the same failure.
        runner.run(task.job_id, task.user_id)

    @app.post(
        "/internal/media/capture",
        response_model=CaptureResponse,
        dependencies=[Depends(require_api_service)],
    )
    def capture(
        body: CaptureRequest, settings: Annotated[Settings, Depends(get_settings)]
    ) -> CaptureResponse:
        """Frame extraction for the API. The API authorized the request and chose the key."""
        from pipe1_server import media

        if not body.object_key.startswith("org/"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT)
        store = build_store(settings)
        try:
            png = media.extract_frame_png(media_source(store, body.object_key), body.timestamp_ms)
        except media.MediaError as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "frame unavailable"
            ) from error
        distance = media.read_distance(png, body.roi) if body.roi else None
        return CaptureResponse(png_base64=base64.b64encode(png).decode("ascii"), distance=distance)

    @app.post(
        "/internal/media/frame",
        response_model=CaptureResponse,
        dependencies=[Depends(require_api_service)],
    )
    def frame(body: FrameRequest) -> CaptureResponse:
        """Validate and re-encode a frame the browser captured, and read its depth counter."""
        from pipe1_server import media

        try:
            png = media.normalize_frame_png(base64.b64decode(body.png_base64, validate=True))
        except (ValueError, media.MediaError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid frame") from error
        distance = media.read_distance(png, body.roi) if body.roi else None
        return CaptureResponse(png_base64=base64.b64encode(png).decode("ascii"), distance=distance)

    @app.get("/internal/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_worker_app()
