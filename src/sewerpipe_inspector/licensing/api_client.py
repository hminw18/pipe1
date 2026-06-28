from __future__ import annotations

from typing import Protocol
from urllib.parse import urlparse

import httpx

from sewerpipe_inspector.licensing.errors import LicenseApiError, LicenseConnectionError


class LicenseApiClientProtocol(Protocol):
    def activate(
        self,
        *,
        license_key: str,
        device_id: str,
        device_name: str | None,
        os_name: str,
        os_version: str,
        app_version: str,
    ) -> dict:
        ...

    def validate(
        self,
        *,
        activation_id: str,
        device_id: str,
        app_version: str,
    ) -> dict:
        ...


class HttpLicenseApiClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        _validate_secure_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def activate(
        self,
        *,
        license_key: str,
        device_id: str,
        device_name: str | None,
        os_name: str,
        os_version: str,
        app_version: str,
    ) -> dict:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/licenses/activate",
                    json={
                        "license_key": license_key,
                        "device_id": device_id,
                        "device_name": device_name,
                        "os_name": os_name,
                        "os_version": os_version,
                        "app_version": app_version,
                    },
                )
        except httpx.RequestError as exc:
            raise LicenseConnectionError("라이선스 서버에 연결할 수 없습니다.") from exc
        _raise_for_status(response)
        return response.json()

    def validate(
        self,
        *,
        activation_id: str,
        device_id: str,
        app_version: str,
    ) -> dict:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/licenses/validate",
                    json={
                        "activation_id": activation_id,
                        "device_id": device_id,
                        "app_version": app_version,
                    },
                )
        except httpx.RequestError as exc:
            raise LicenseConnectionError("라이선스 서버에 연결할 수 없습니다.") from exc
        _raise_for_status(response)
        return response.json()


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    code = payload.get("code") if isinstance(payload, dict) else None
    message = payload.get("message") if isinstance(payload, dict) else None
    raise LicenseApiError(
        str(code or "SERVER_ERROR"),
        str(message or "서버 오류가 발생했습니다."),
        response.status_code,
    )


def _validate_secure_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("Production API base URL must use HTTPS.")
