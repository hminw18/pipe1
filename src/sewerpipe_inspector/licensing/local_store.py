from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from sewerpipe_inspector.licensing.dpapi import DpapiError, DpapiProtector


STATE_FORMAT = "pipe1.license_state.dpapi.v1"


class LicenseStateProtectionError(RuntimeError):
    pass


class StateProtector:
    def protect(self, data: bytes) -> bytes:
        raise NotImplementedError

    def unprotect(self, protected_data: bytes) -> bytes:
        raise NotImplementedError


class ProtectedJsonStore:
    def __init__(
        self,
        path: Path,
        state_format: str,
        protector: StateProtector | None = None,
    ) -> None:
        self.path = path
        self.state_format = state_format
        self.protector = protector or DpapiProtector()

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(envelope, dict):
            return None
        if envelope.get("format") != self.state_format:
            return None
        protected_blob = envelope.get("blob")
        if not isinstance(protected_blob, str) or not protected_blob:
            return None
        try:
            protected_data = base64.b64decode(
                protected_blob.encode("ascii"), validate=True
            )
            raw_data = self.protector.unprotect(protected_data)
            state = json.loads(raw_data.decode("utf-8"))
        except (DpapiError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        return state if isinstance(state, dict) else None

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw_data = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            protected_data = self.protector.protect(raw_data)
        except DpapiError as exc:
            raise LicenseStateProtectionError("state protection failed") from exc
        envelope = {
            "format": self.state_format,
            "blob": base64.b64encode(protected_data).decode("ascii"),
        }
        self.path.write_text(
            json.dumps(envelope, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class LocalLicenseStore:
    def __init__(
        self,
        path: Path,
        protector: StateProtector | None = None,
    ) -> None:
        self.path = path
        self.store = ProtectedJsonStore(path, STATE_FORMAT, protector)

    def load(self) -> dict[str, Any] | None:
        return self.store.load()

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
        self.store.save(payload)

    def clear(self) -> None:
        self.store.clear()
