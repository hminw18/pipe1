from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pipe1_server.api import (
    accounts,
    admin,
    auth,
    defects,
    dev_storage,
    jobs,
    projects,
    reports,
    suggestions,
    taxonomy,
    videos,
)
from pipe1_server.api.middleware import build_security_middleware
from pipe1_server.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def create_app(settings_provider: Callable[[], Settings] = get_settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings_provider().job_queue == "background":
            from pipe1_server.api.deps import background_job_queue
            from pipe1_server.db import get_session_factory

            try:
                background_job_queue().resume_unfinished(get_session_factory())
            except Exception:  # noqa: BLE001 - never block startup on this
                logger.exception("could not resume unfinished jobs")
        yield

    app = FastAPI(
        title="Pipe1 API",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.middleware("http")(build_security_middleware(settings_provider))
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(admin.router)
    app.include_router(admin.platform_router)
    app.include_router(projects.router)
    app.include_router(reports.router)
    app.include_router(videos.router)
    app.include_router(defects.router)
    app.include_router(jobs.router)
    app.include_router(suggestions.router)
    app.include_router(taxonomy.router)
    if settings_provider().storage_backend == "local":
        app.include_router(dev_storage.router)

    @app.get("/api/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
