from __future__ import annotations

import platform
from pathlib import Path
from uuid import uuid4

from sewerpipe_inspector.licensing.local_store import (
    ProtectedJsonStore,
    StateProtector,
)


DEVICE_ID_FORMAT = "pipe1.device_id.dpapi.v1"


class DeviceIdentity:
    def __init__(
        self,
        path: Path,
        protector: StateProtector | None = None,
    ) -> None:
        self.path = path
        self.store = ProtectedJsonStore(path, DEVICE_ID_FORMAT, protector)

    def get_or_create(self, forced: str | None = None) -> str:
        if forced:
            self._save(forced)
            return forced
        state = self.store.load()
        if state is not None:
            value = state.get("device_id")
            if isinstance(value, str) and value:
                return value
        device_id = f"pipe1-{uuid4().hex}"
        self._save(device_id)
        return device_id

    def _save(self, device_id: str) -> None:
        self.store.save({"device_id": device_id})

    @staticmethod
    def device_name() -> str | None:
        name = platform.node().strip()
        return name or None

    @staticmethod
    def os_name() -> str:
        return platform.system() or "Unknown"

    @staticmethod
    def os_version() -> str:
        return platform.release() or "Unknown"
