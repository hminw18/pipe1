from __future__ import annotations

from PySide6.QtWidgets import QDialog

from sewerpipe_inspector import app as app_module
from sewerpipe_inspector.licensing.config import LicenseRuntimeConfig
from sewerpipe_inspector.licensing.license_service import LicenseStatus
from sewerpipe_inspector.settings_service import AppSettings


class _InactiveLicenseService:
    def __init__(self) -> None:
        self.validate_online_calls: list[bool] = []

    def current_status(self, *, validate_online: bool = False) -> LicenseStatus:
        self.validate_online_calls.append(validate_online)
        return LicenseStatus(status="inactive", reason="license is not activated")


class _GraceExpiredLicenseService:
    def __init__(self) -> None:
        self.validate_online_calls: list[bool] = []

    def current_status(self, *, validate_online: bool = False) -> LicenseStatus:
        self.validate_online_calls.append(validate_online)
        if validate_online:
            return LicenseStatus(
                status="active",
                masked_license_key="PIPE1-ABCD",
                features={"local_report": True},
            )
        return LicenseStatus(
            status="invalid",
            reason="offline grace period has expired",
        )


class _DialogShouldNotOpen:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("license activation dialog should not open in dev mode")


class _StatusLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


class _AcceptedActivationDialog:
    created: list["_AcceptedActivationDialog"] = []

    def __init__(self, _service: object) -> None:
        self.status_label = _StatusLabel()
        self.license_status = LicenseStatus(
            status="active",
            masked_license_key="PIPE1-NEWK",
            features={"local_report": True},
        )
        self.created.append(self)

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted


class _RejectedActivationDialog:
    created: list["_RejectedActivationDialog"] = []

    def __init__(self, _service: object) -> None:
        self.status_label = _StatusLabel()
        self.license_status = None
        self.created.append(self)

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Rejected


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int | None]] = []

    def showMessage(self, message: str, timeout: int | None = None) -> None:
        self.messages.append((message, timeout))


class _Inspection:
    def __init__(self) -> None:
        self.training_upload_service = object()


class _Window:
    def __init__(self) -> None:
        self.license_status: LicenseStatus | None = None
        self.inspection = _Inspection()
        self.status_bar = _StatusBar()
        self.closed = False
        self.enabled_values: list[bool] = []
        self.update_info = None
        self.prompt_update_calls: list[tuple[object, bool, bool]] = []
        self.mandatory_update_calls: list[object] = []

    def statusBar(self) -> _StatusBar:
        return self.status_bar

    def set_update_info(self, info: object) -> None:
        self.update_info = info

    def prompt_update(
        self,
        info: object,
        *,
        mandatory: bool = False,
        allow_suppress: bool = False,
    ) -> None:
        self.prompt_update_calls.append((info, mandatory, allow_suppress))

    def start_mandatory_update(self, info: object) -> None:
        self.mandatory_update_calls.append(info)

    def setEnabled(self, enabled: bool) -> None:
        self.enabled_values.append(enabled)

    def close(self) -> None:
        self.closed = True


def _update_info(
    *,
    latest_version: str = "0.1.1",
    mandatory: bool = False,
) -> app_module.UpdateInfo:
    return app_module.UpdateInfo(
        update_available=True,
        current_version="0.1.0",
        latest_version=latest_version,
        mandatory=mandatory,
        min_supported_version=None,
        download_url=f"https://pipe1dev.cloud/downloads/PIPE1-{latest_version}.msi",
        sha256="a" * 64,
        size_bytes=123,
        release_notes=None,
        published_at="2026-06-29T00:00:00Z",
    )


def _dispatch_update_check(window: _Window, info: app_module.UpdateInfo) -> None:
    handler = type("Handler", (), {"window": window})()
    app_module._UpdateCheckHandler.on_succeeded(handler, info)


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
    assert service.validate_online_calls == [False]


def test_startup_uses_online_validation_only_when_local_grace_expired(
    monkeypatch,
    tmp_path,
) -> None:
    config = LicenseRuntimeConfig(
        api_base_url="https://license.example.com",
        public_keys={"kid": "public"},
        app_env="production",
        app_version="0.1.0",
        state_dir=tmp_path,
        require_activation=True,
    )
    service = _GraceExpiredLicenseService()
    monkeypatch.setattr(app_module, "load_license_runtime_config", lambda: config)
    monkeypatch.setattr(app_module, "build_license_service", lambda _config: service)
    monkeypatch.setattr(app_module, "LicenseActivationDialog", _DialogShouldNotOpen)

    license_service, license_status, license_config = (
        app_module._ensure_license_activation()
    )

    assert license_service is service
    assert license_status is not None
    assert license_status.status == "active"
    assert license_config is config
    assert service.validate_online_calls == [False, True]


def test_background_validation_failure_prompts_for_replacement_key(
    monkeypatch,
) -> None:
    _AcceptedActivationDialog.created = []
    window = _Window()
    service = object()
    monkeypatch.setattr(
        app_module,
        "LicenseActivationDialog",
        _AcceptedActivationDialog,
    )

    app_module._handle_background_license_status(
        service,  # type: ignore[arg-type]
        window,  # type: ignore[arg-type]
        LicenseStatus(status="invalid", reason="Activation is inactive."),
        require_activation=True,
    )

    assert window.license_status is not None
    assert window.license_status.status == "active"
    assert window.inspection.training_upload_service is None
    assert _AcceptedActivationDialog.created
    assert "Activation is inactive." in _AcceptedActivationDialog.created[0].status_label.text
    assert any("라이선스 갱신 완료" in message for message, _ in window.status_bar.messages)


def test_background_validation_failure_exit_when_reactivation_is_cancelled(
    monkeypatch,
) -> None:
    _RejectedActivationDialog.created = []
    window = _Window()
    service = object()
    monkeypatch.setattr(
        app_module,
        "LicenseActivationDialog",
        _RejectedActivationDialog,
    )

    app_module._handle_background_license_status(
        service,  # type: ignore[arg-type]
        window,  # type: ignore[arg-type]
        LicenseStatus(status="invalid", reason="Activation is inactive."),
        require_activation=True,
    )

    assert window.closed is True
    assert window.enabled_values == [False]
    assert any(
        "라이선스가 갱신되지 않았습니다" in message
        for message, _ in window.status_bar.messages
    )


def test_optional_startup_update_prompts_when_not_suppressed(monkeypatch) -> None:
    window = _Window()
    info = _update_info(mandatory=False)
    monkeypatch.setattr(app_module, "load_settings", lambda: AppSettings())

    _dispatch_update_check(window, info)

    assert window.update_info is info
    assert window.prompt_update_calls == [(info, False, True)]
    assert window.mandatory_update_calls == []


def test_optional_startup_update_does_not_prompt_for_suppressed_version(
    monkeypatch,
) -> None:
    window = _Window()
    info = _update_info(latest_version="0.1.1", mandatory=False)
    monkeypatch.setattr(
        app_module,
        "load_settings",
        lambda: AppSettings(suppressed_update_prompt_version="0.1.1"),
    )

    _dispatch_update_check(window, info)

    assert window.update_info is info
    assert window.prompt_update_calls == []
    assert window.mandatory_update_calls == []
    assert window.status_bar.messages


def test_optional_startup_update_prompts_again_for_next_version(monkeypatch) -> None:
    window = _Window()
    info = _update_info(latest_version="0.1.2", mandatory=False)
    monkeypatch.setattr(
        app_module,
        "load_settings",
        lambda: AppSettings(suppressed_update_prompt_version="0.1.1"),
    )

    _dispatch_update_check(window, info)

    assert window.update_info is info
    assert window.prompt_update_calls == [(info, False, True)]
    assert window.mandatory_update_calls == []
