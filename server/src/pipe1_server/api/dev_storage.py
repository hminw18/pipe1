"""Signed-URL endpoints for LocalObjectStore. Mounted only with the local storage backend,
which settings forbid outside dev; production uses Cloud Storage URLs directly."""

from __future__ import annotations

import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from pipe1_server.settings import Settings, get_settings
from pipe1_server.storage import LocalObjectStore, build_store

router = APIRouter(prefix="/api/dev-storage", include_in_schema=False)


def _store(settings: Annotated[Settings, Depends(get_settings)]) -> LocalObjectStore:
    store = build_store(settings)
    if not isinstance(store, LocalObjectStore):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return store


def _check(
    store: LocalObjectStore, method: str, key: str, expires: int, extra: str, sig: str
) -> None:
    if expires < time.time() or not hmac.compare_digest(
        store.sign(method, key, expires, extra), sig
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN)


@router.get("/get")
def download(
    store: Annotated[LocalObjectStore, Depends(_store)],
    key: str,
    expires: int,
    signature: str,
    disposition: str = "",
) -> FileResponse:
    _check(store, "GET", key, expires, disposition, signature)
    path = store.path_for(key)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    headers = {"Content-Disposition": disposition} if disposition else None
    return FileResponse(path, headers=headers)


@router.put("/put", status_code=status.HTTP_200_OK)
async def upload(
    request: Request,
    store: Annotated[LocalObjectStore, Depends(_store)],
    key: str,
    expires: int,
    signature: str,
    content_type: Annotated[str, Query()],
    max_bytes: int,
) -> Response:
    _check(store, "PUT", key, expires, f"{content_type}\n{max_bytes}", signature)
    if request.headers.get("content-type", "").split(";")[0] != content_type:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    path = store.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("wb") as file:
        async for chunk in request.stream():
            written += len(chunk)
            if written > max_bytes:
                file.close()
                path.unlink(missing_ok=True)
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE)
            file.write(chunk)
    return Response(status_code=status.HTTP_200_OK)
