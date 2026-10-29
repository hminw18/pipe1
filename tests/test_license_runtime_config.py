from __future__ import annotations

from sewerpipe_inspector.licensing.config import load_license_runtime_config


def test_production_environment_requires_license_activation() -> None:
    config = load_license_runtime_config({"PIPE1_APP_ENV": "prod"})

    assert config.app_env == "prod"
    assert config.require_activation is True
    assert config.update_check_enabled is True
    assert not config.is_configured
