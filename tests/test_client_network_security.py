from __future__ import annotations

import pytest

from sewerpipe_inspector.licensing.api_client import HttpLicenseApiClient
from sewerpipe_inspector.services.training_upload_service import TrainingUploadClient


def test_external_http_base_urls_are_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HttpLicenseApiClient("http://license.example.com")
    with pytest.raises(ValueError, match="HTTPS"):
        TrainingUploadClient("http://license.example.com")

    HttpLicenseApiClient("http://localhost:8000")
    TrainingUploadClient("http://127.0.0.1:8000")
