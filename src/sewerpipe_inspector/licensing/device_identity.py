from __future__ import annotations

import platform
from pathlib import Path
from uuid import uuid4


class DeviceIdentity:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_or_create(self, forced: str | None = None) -> str:
        if forced:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(forced, encoding="utf-8")
            return forced
        if self.path.exists():
            value = self.path.read_text(encoding="utf-8").strip()
            if value:
                return value
        device_id = f"pipe1-{uuid4().hex}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(device_id, encoding="utf-8")
        return device_id

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
