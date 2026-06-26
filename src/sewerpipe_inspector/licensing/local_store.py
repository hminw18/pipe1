from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LocalLicenseStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save_activation_state(
        self,
        *,
        activation_id: str,
        masked_license_key: str,
        entitlement: dict[str, Any],
        device_upload_token: str | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "activation_id": activation_id,
            "masked_license_key": masked_license_key,
            "entitlement": entitlement,
            "device_upload_token": device_upload_token,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
