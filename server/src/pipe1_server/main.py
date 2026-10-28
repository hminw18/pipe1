from __future__ import annotations

from collections.abc import Callable

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
    taxonomy,
    videos,
)
from pipe1_server.api.middleware import build_security_middleware
from pipe1_server.settings import Settings, get_settings


def create_app(settings_provider: Callable[[], Settings] = get_settings) -> FastAPI:
    app = FastAPI(title="Pipe1 API", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
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
    app.include_router(taxonomy.router)
    if settings_provider().storage_backend == "local":
        app.include_router(dev_storage.router)

    @app.get("/api/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
