from __future__ import annotations

from sewerpipe_inspector import app as app_module
from sewerpipe_inspector.licensing.config import LicenseRuntimeConfig
from sewerpipe_inspector.licensing.license_service import LicenseStatus


class _InactiveLicenseService:
    def current_status(self) -> LicenseStatus:
        return LicenseStatus(status="inactive", reason="license is not activated")


class _DialogShouldNotOpen:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("license activation dialog should not open in dev mode")


def test_dev_mode_does_not_prompt_for_activation_when_server_is_configured(
    monkeypatch,
    tmp_path,
) -> None:
    config = LicenseRuntimeConfig(
        api_base_url="https://license.example.com",
        public_keys={"kid": "public"},
        app_env="dev",
        app_version="0.1.0",
        state_dir=tmp_path,
        require_activation=False,
    )
    service = _InactiveLicenseService()
    monkeypatch.setattr(app_module, "load_license_runtime_config", lambda: config)
    monkeypatch.setattr(app_module, "build_license_service", lambda _config: service)
    monkeypatch.setattr(app_module, "LicenseActivationDialog", _DialogShouldNotOpen)

    license_service, license_status, license_config = (
        app_module._ensure_license_activation()
    )

    assert license_service is service
    assert license_status is None
    assert license_config is config
