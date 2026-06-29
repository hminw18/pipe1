from __future__ import annotations

import pytest
import httpx

from sewerpipe_inspector.licensing.api_client import HttpLicenseApiClient, _json_dict
from sewerpipe_inspector.licensing.errors import LicenseConnectionError
from sewerpipe_inspector.services.training_upload_service import TrainingUploadClient


def test_external_http_base_urls_are_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HttpLicenseApiClient("http://license.example.com")
    with pytest.raises(ValueError, match="HTTPS"):
        TrainingUploadClient("http://license.example.com")

    HttpLicenseApiClient("http://localhost:8000")
    TrainingUploadClient("http://127.0.0.1:8000")


def test_license_api_rejects_malformed_success_payload_as_connection_error() -> None:
    response = httpx.Response(200, content=b"not-json")

    with pytest.raises(LicenseConnectionError):
        _json_dict(response)


def test_license_api_rejects_non_object_success_payload() -> None:
    response = httpx.Response(200, json=["not", "an", "object"])

    with pytest.raises(LicenseConnectionError):
        _json_dict(response)
