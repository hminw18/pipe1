from __future__ import annotations

import sys
from pathlib import Path

from sewerpipe_inspector import resources
from sewerpipe_inspector.licensing import config as license_config


def test_find_resource_checks_pyinstaller_bundle_root(
    tmp_path: Path, monkeypatch
) -> None:
    bundle_root = tmp_path / "_internal"
    bundle_root.mkdir()
    pipe_png = bundle_root / "pipe.png"
    pipe_png.write_bytes(b"png")

    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert resources.find_resource("pipe.png") == pipe_png


def test_logo_resource_can_load_from_pyinstaller_bundle_root(
    tmp_path: Path, monkeypatch
) -> None:
    bundle_root = tmp_path / "_internal"
    bundle_root.mkdir()
    logo_png = bundle_root / "logo.png"
    logo_png.write_bytes(b"png")

    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert resources.find_resource("logo.png") == logo_png


def test_license_env_can_load_from_resource_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / "pipe1.client.env"
    env_file.write_text(
        "\n".join(
            [
                "PIPE1_APP_ENV=production",
                "PIPE1_APP_VERSION=2.0.0",
                "PIPE1_LICENSE_API_BASE_URL=https://license.example.com",
                'PIPE1_LICENSE_PUBLIC_KEYS={"kid":"public"}',
            ]
        ),
        encoding="utf-8",
    )

    def fake_resource_path_candidates(relative_path: str | Path) -> list[Path]:
        if Path(relative_path) == license_config.DEFAULT_CLIENT_ENV_PATH:
            return [env_file]
        return []

    monkeypatch.delenv("PIPE1_CLIENT_ENV_FILE", raising=False)
    monkeypatch.delenv("PIPE1_APP_ENV", raising=False)
    monkeypatch.delenv("PIPE1_LICENSE_API_BASE_URL", raising=False)
    monkeypatch.delenv("PIPE1_LICENSE_PUBLIC_KEYS", raising=False)
    monkeypatch.setattr(
        license_config,
        "resource_path_candidates",
        fake_resource_path_candidates,
    )

    config = license_config.load_license_runtime_config()

    assert config.app_env == "production"
    assert config.app_version == "2.0.0"
    assert config.api_base_url == "https://license.example.com"
    assert config.public_keys == {"kid": "public"}
    assert config.require_activation is True
