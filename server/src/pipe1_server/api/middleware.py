"""Browser-security middleware (docs/Web_Auth_Spec.md §7)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from pipe1_server.api.deps import UNSAFE_METHODS
from pipe1_server.settings import Settings

STORAGE_ORIGIN = "https://storage.googleapis.com"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        f"img-src 'self' {STORAGE_ORIGIN}",
        f"media-src 'self' blob: {STORAGE_ORIGIN}",
        f"connect-src 'self' {STORAGE_ORIGIN}",
        "style-src 'self'",
        "script-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

# Endpoints that legitimately take a non-JSON body. The dev storage upload is authorized by
# its URL signature, like a Cloud Storage signed URL.
NON_JSON_PATHS: frozenset[str] = frozenset({"/api/dev-storage/put"})


def _is_json(content_type: str | None) -> bool:
    return (content_type or "").split(";", 1)[0].strip().lower() == "application/json"


def _json_or_empty(request: Request) -> bool:
    """Bodies must be JSON; a request without a body (e.g. DELETE) needs no Content-Type.

    HTML forms always send a form Content-Type, so they are still rejected.
    """
    content_type = request.headers.get("content-type")
    if content_type is not None:
        return _is_json(content_type)
    has_body = request.headers.get("content-length", "0") != "0" or (
        "transfer-encoding" in request.headers
    )
    return not has_body


def build_security_middleware(
    settings_provider: Callable[[], Settings],
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def security_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        is_api = request.url.path.startswith("/api/")
        response: Response
        if is_api and request.method in UNSAFE_METHODS:
            settings = settings_provider()
            if request.headers.get("origin") != settings.app_origin:
                response = JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            elif request.url.path not in NON_JSON_PATHS and not _json_or_empty(request):
                response = JSONResponse(
                    {"detail": "Content-Type must be application/json"}, status_code=415
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)

        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if is_api:
            response.headers["Cache-Control"] = "no-store"
        return response

    return security_middleware
