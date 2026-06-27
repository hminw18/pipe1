from __future__ import annotations

from pathlib import Path

from sewerpipe_inspector.licensing.config import load_license_runtime_config


def test_production_environment_requires_license_activation() -> None:
    config = load_license_runtime_config({"PIPE1_APP_ENV": "prod"})

    assert config.app_env == "prod"
    assert config.require_activation is True
    assert not config.is_configured


def test_development_environment_keeps_activation_optional() -> None:
    config = load_license_runtime_config({"PIPE1_APP_ENV": "dev"})

    assert config.app_env == "dev"
    assert config.require_activation is False
    assert not config.is_configured


def test_explicit_activation_flag_still_forces_activation() -> None:
    config = load_license_runtime_config(
        {
            "PIPE1_APP_ENV": "dev",
            "PIPE1_REQUIRE_LICENSE_ACTIVATION": "true",
        }
    )

    assert config.require_activation is True


def test_production_configured_license_runtime_is_ready() -> None:
    config = load_license_runtime_config(
        {
            "PIPE1_APP_ENV": "production",
            "PIPE1_LICENSE_API_BASE_URL": "https://license.example.com",
            "PIPE1_LICENSE_PUBLIC_KEYS": '{"license-signing-key-001":"public"}',
        }
    )

    assert config.app_env == "production"
    assert config.require_activation is True
    assert config.is_configured


def test_client_env_file_is_loaded_when_explicitly_configured(tmp_path: Path) -> None:
    env_file = tmp_path / "pipe1.client.env"
    env_file.write_text(
        "\n".join(
            [
                "PIPE1_APP_ENV=production",
                "PIPE1_APP_VERSION=1.2.3",
                "PIPE1_LICENSE_API_BASE_URL=https://license.example.com",
                'PIPE1_LICENSE_PUBLIC_KEYS={"license-signing-key-001":"public"}',
            ]
        ),
        encoding="utf-8",
    )

    config = load_license_runtime_config({"PIPE1_CLIENT_ENV_FILE": str(env_file)})

    assert config.app_env == "production"
    assert config.app_version == "1.2.3"
    assert config.api_base_url == "https://license.example.com"
    assert config.public_keys == {"license-signing-key-001": "public"}
    assert config.require_activation is True
    assert config.is_configured


def test_os_environment_overrides_client_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "pipe1.client.env"
    env_file.write_text(
        "\n".join(
            [
                "PIPE1_APP_ENV=production",
                "PIPE1_LICENSE_API_BASE_URL=https://license.example.com",
                'PIPE1_LICENSE_PUBLIC_KEYS={"license-signing-key-001":"public"}',
            ]
        ),
        encoding="utf-8",
    )

    config = load_license_runtime_config(
        {
            "PIPE1_CLIENT_ENV_FILE": str(env_file),
            "PIPE1_APP_ENV": "dev",
            "PIPE1_REQUIRE_LICENSE_ACTIVATION": "false",
        }
    )

    assert config.app_env == "dev"
    assert config.require_activation is False
    assert config.is_configured
