from __future__ import annotations

from typing import Protocol
from urllib.parse import urlparse

import httpx

from sewerpipe_inspector.updates.models import UpdateInfo


class UpdateConnectionError(RuntimeError):
    pass


class UpdateApiClientProtocol(Protocol):
    def latest_release(
        self,
        *,
        platform: str,
        arch: str,
        channel: str,
        current_version: str,
    ) -> UpdateInfo:
        ...


class HttpUpdateApiClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        _validate_secure_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def allowed_download_host(self) -> str:
        parsed = urlparse(self.base_url)
        return parsed.hostname or ""

    def latest_release(
        self,
        *,
        platform: str,
        arch: str,
        channel: str,
        current_version: str,
    ) -> UpdateInfo:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    f"{self.base_url}/app/releases/latest",
                    params={
                        "platform": platform,
                        "arch": arch,
                        "channel": channel,
                        "current_version": current_version,
                    },
                )
        except httpx.RequestError as exc:
            raise UpdateConnectionError("업데이트 서버에 연결할 수 없습니다.") from exc
        if not response.is_success:
            raise UpdateConnectionError("업데이트 정보를 확인할 수 없습니다.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpdateConnectionError("업데이트 서버 응답을 해석할 수 없습니다.") from exc
        if not isinstance(payload, dict):
            raise UpdateConnectionError("업데이트 서버 응답 형식이 올바르지 않습니다.")
        try:
            info = UpdateInfo.from_payload(payload)
            info.require_download_metadata()
        except ValueError as exc:
            raise UpdateConnectionError(str(exc)) from exc
        return info


def _validate_secure_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("Production update API base URL must use HTTPS.")
