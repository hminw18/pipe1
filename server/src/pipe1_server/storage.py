"""Object storage for videos, captures and exports (docs/Web_Architecture.md §4-§5).

Keys are always chosen by the server; clients never supply storage paths. Downloads and
uploads go directly between browser and storage with short-lived signed URLs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import shutil
import time
import uuid
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlencode

from pipe1_server.settings import Settings

IMAGE_URL_TTL = timedelta(minutes=5)
VIDEO_URL_TTL = timedelta(minutes=60)
UPLOAD_SESSION_TTL = timedelta(minutes=30)


def project_prefix(organization_id: uuid.UUID, project_id: uuid.UUID) -> str:
    return f"org/{organization_id}/project/{project_id}/"


def report_prefix(organization_id: uuid.UUID, project_id: uuid.UUID, report_id: uuid.UUID) -> str:
    return f"{project_prefix(organization_id, project_id)}report/{report_id}/"


class ObjectStore(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    def download_to(self, key: str, path: Path) -> None: ...

    def upload_from(self, path: Path, key: str, content_type: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def size(self, key: str) -> int | None: ...

    def copy(self, source: str, destination: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def signed_download_url(
        self, key: str, ttl: timedelta, *, filename: str | None = None, attachment: bool = False
    ) -> str: ...

    def signed_upload_url(self, key: str, content_type: str, max_bytes: int) -> str: ...


def _validate_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise ValueError(f"invalid object key: {key!r}")
    return key


def _content_disposition(filename: str | None, attachment: bool) -> str | None:
    if filename is None and not attachment:
        return None
    kind = "attachment" if attachment else "inline"
    if filename is None:
        return kind
    ascii_name = filename.encode("ascii", "ignore").decode() or "download"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


class GcsObjectStore:
    """Google Cloud Storage. On Cloud Run, URLs are signed through the IAM signBlob API
    using the runtime service account (needs roles/iam.serviceAccountTokenCreator on itself).
    """

    def __init__(self, bucket: str, signer_email: str | None) -> None:
        import google.auth
        from google.cloud import storage

        self._credentials, project = google.auth.default()
        self._client = storage.Client(credentials=self._credentials, project=project)
        self._bucket = self._client.bucket(bucket)
        self._signer_email = signer_email

    def _signing_kwargs(self) -> dict[str, object]:
        if self._signer_email is None:
            return {}
        from google.auth.transport.requests import Request

        if not self._credentials.valid:
            self._credentials.refresh(Request())
        return {
            "service_account_email": self._signer_email,
            "access_token": self._credentials.token,
        }

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._bucket.blob(_validate_key(key)).upload_from_string(data, content_type=content_type)

    def download_to(self, key: str, path: Path) -> None:
        self._bucket.blob(_validate_key(key)).download_to_filename(str(path))

    def upload_from(self, path: Path, key: str, content_type: str) -> None:
        self._bucket.blob(_validate_key(key)).upload_from_filename(
            str(path), content_type=content_type
        )

    def exists(self, key: str) -> bool:
        return self._bucket.blob(_validate_key(key)).exists()

    def size(self, key: str) -> int | None:
        blob = self._bucket.get_blob(_validate_key(key))
        return blob.size if blob is not None else None

    def copy(self, source: str, destination: str) -> None:
        self._bucket.copy_blob(
            self._bucket.blob(_validate_key(source)), self._bucket, _validate_key(destination)
        )

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket.blob(_validate_key(key)).delete()
        except NotFound:
            pass

    def delete_prefix(self, prefix: str) -> None:
        if not prefix.endswith("/"):
            raise ValueError("prefix must end with /")
        for blob in self._client.list_blobs(self._bucket, prefix=_validate_key(prefix)):
            blob.delete()

    def signed_download_url(
        self, key: str, ttl: timedelta, *, filename: str | None = None, attachment: bool = False
    ) -> str:
        return self._bucket.blob(_validate_key(key)).generate_signed_url(
            version="v4",
            expiration=ttl,
            method="GET",
            response_disposition=_content_disposition(filename, attachment),
            scheme="https",
            **self._signing_kwargs(),
        )

    def signed_upload_url(self, key: str, content_type: str, max_bytes: int) -> str:
        """URL that starts a resumable upload session (POST with x-goog-resumable: start).

        The signed headers pin the content type and a maximum size.
        """
        return self._bucket.blob(_validate_key(key)).generate_signed_url(
            version="v4",
            expiration=UPLOAD_SESSION_TTL,
            method="POST",
            content_type=content_type,
            headers={
                "x-goog-resumable": "start",
                "x-goog-content-length-range": f"0,{max_bytes}",
            },
            scheme="https",
            **self._signing_kwargs(),
        )


class LocalObjectStore:
    """Development and tests: files under a directory, URLs served by the dev storage route
    and signed with HMAC so they expire like GCS URLs."""

    def __init__(self, root: Path, base_url: str, signing_key: str) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url.rstrip("/")
        self._key = signing_key.encode("utf-8")

    def path_for(self, key: str) -> Path:
        path = (self._root / _validate_key(key)).resolve()
        if self._root not in path.parents:
            raise ValueError(f"invalid object key: {key!r}")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def download_to(self, key: str, path: Path) -> None:
        shutil.copyfile(self.path_for(key), path)

    def upload_from(self, path: Path, key: str, content_type: str) -> None:
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def size(self, key: str) -> int | None:
        path = self.path_for(key)
        return path.stat().st_size if path.is_file() else None

    def copy(self, source: str, destination: str) -> None:
        target = self.path_for(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path_for(source), target)

    def delete(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        if not prefix.endswith("/"):
            raise ValueError("prefix must end with /")
        directory = self.path_for(prefix.rstrip("/"))
        if directory.is_dir():
            shutil.rmtree(directory)

    def sign(self, method: str, key: str, expires: int, extra: str = "") -> str:
        message = f"{method}\n{key}\n{expires}\n{extra}".encode()
        digest = hmac.new(self._key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def _url(self, method: str, key: str, ttl: timedelta, extra: str = "", **params: str) -> str:
        expires = int(time.time() + ttl.total_seconds())
        query = {"key": key, "expires": str(expires), **params}
        query["signature"] = self.sign(method, key, expires, extra)
        return f"{self._base_url}/api/dev-storage/{method.lower()}?{urlencode(query)}"

    def signed_download_url(
        self, key: str, ttl: timedelta, *, filename: str | None = None, attachment: bool = False
    ) -> str:
        disposition = _content_disposition(filename, attachment) or ""
        return self._url("GET", _validate_key(key), ttl, disposition, disposition=disposition)

    def signed_upload_url(self, key: str, content_type: str, max_bytes: int) -> str:
        extra = f"{content_type}\n{max_bytes}"
        return self._url(
            "PUT",
            _validate_key(key),
            UPLOAD_SESSION_TTL,
            extra,
            content_type=content_type,
            max_bytes=str(max_bytes),
        )


@lru_cache
def _store(backend: str, bucket: str | None, signer: str | None, root: str, base: str, key: str):
    if backend == "gcs":
        if not bucket:
            raise RuntimeError("PIPE1_MEDIA_BUCKET is required for the gcs storage backend")
        return GcsObjectStore(bucket, signer)
    return LocalObjectStore(Path(root), base, key)


def build_store(settings: Settings) -> ObjectStore:
    return _store(
        settings.storage_backend,
        settings.media_bucket,
        settings.storage_signer_email,
        settings.local_storage_dir,
        settings.public_api_origin or settings.app_origin,
        settings.csrf_key.get_secret_value(),
    )


def report_group_prefix(
    organization_id: uuid.UUID, project_id: uuid.UUID, report_group_id: uuid.UUID
) -> str:
    """Videos live here: all versions of a report share one upload."""
    return f"{project_prefix(organization_id, project_id)}report-group/{report_group_id}/"
