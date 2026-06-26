from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from sewerpipe_inspector.licensing.api_client import HttpLicenseApiClient
from sewerpipe_inspector.licensing.device_identity import DeviceIdentity
from sewerpipe_inspector.licensing.entitlement import EntitlementVerifier
from sewerpipe_inspector.licensing.errors import LicenseConfigurationError
from sewerpipe_inspector.licensing.license_service import LicenseService
from sewerpipe_inspector.licensing.local_store import LocalLicenseStore


@dataclass(frozen=True)
class LicenseRuntimeConfig:
    api_base_url: str | None
    public_keys: dict[str, str]
    app_version: str
    state_dir: Path
    require_activation: bool

    @property
    def is_configured(self) -> bool:
        return bool(self.api_base_url and self.public_keys)


def default_license_state_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Pipe1"
    return Path.home() / ".pipe1"


def load_license_runtime_config(
    environ: Mapping[str, str] | None = None,
) -> LicenseRuntimeConfig:
    values = environ or os.environ
    public_keys_raw = values.get("PIPE1_LICENSE_PUBLIC_KEYS", "").strip()
    public_keys: dict[str, str] = {}
    if public_keys_raw:
        try:
            loaded = json.loads(public_keys_raw)
        except json.JSONDecodeError as exc:
            raise LicenseConfigurationError("PIPE1_LICENSE_PUBLIC_KEYS must be JSON") from exc
        if not isinstance(loaded, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in loaded.items()
        ):
            raise LicenseConfigurationError(
                "PIPE1_LICENSE_PUBLIC_KEYS must be a JSON object of kid to key"
            )
        public_keys = dict(loaded)

    state_dir_raw = values.get("PIPE1_LICENSE_STATE_DIR")
    return LicenseRuntimeConfig(
        api_base_url=values.get("PIPE1_LICENSE_API_BASE_URL"),
        public_keys=public_keys,
        app_version=values.get("PIPE1_APP_VERSION", "0.1.0"),
        state_dir=Path(state_dir_raw) if state_dir_raw else default_license_state_dir(),
        require_activation=values.get("PIPE1_REQUIRE_LICENSE_ACTIVATION", "").lower()
        in {"1", "true", "yes"},
    )


def build_license_service(config: LicenseRuntimeConfig) -> LicenseService:
    if not config.api_base_url or not config.public_keys:
        raise LicenseConfigurationError("license API URL and public keys are required")
    return LicenseService(
        store=LocalLicenseStore(config.state_dir / "license_state.json"),
        device_identity=DeviceIdentity(config.state_dir / "device_id"),
        client=HttpLicenseApiClient(config.api_base_url),
        verifier=EntitlementVerifier(config.public_keys),
        app_version=config.app_version,
    )
