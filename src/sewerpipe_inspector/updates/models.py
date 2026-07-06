from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateInfo:
    update_available: bool
    current_version: str
    latest_version: str | None
    mandatory: bool
    min_supported_version: str | None
    download_url: str | None
    sha256: str | None
    size_bytes: int | None
    release_notes: str | None
    published_at: str | None

    @classmethod
    def from_payload(cls, payload: dict) -> "UpdateInfo":
        return cls(
            update_available=bool(payload.get("update_available")),
            current_version=str(payload.get("current_version") or ""),
            latest_version=_optional_string(payload.get("latest_version")),
            mandatory=bool(payload.get("mandatory")),
            min_supported_version=_optional_string(payload.get("min_supported_version")),
            download_url=_optional_string(payload.get("download_url")),
            sha256=_optional_string(payload.get("sha256")),
            size_bytes=_optional_int(payload.get("size_bytes")),
            release_notes=_optional_string(payload.get("release_notes")),
            published_at=_optional_string(payload.get("published_at")),
        )

    def require_download_metadata(self) -> None:
        if not self.update_available:
            return
        if not self.latest_version:
            raise ValueError("Update response is missing latest_version.")
        if not self.download_url or not self.sha256:
            raise ValueError("Update response is missing download metadata.")
        if self.size_bytes is None or self.size_bytes <= 0:
            raise ValueError("Update response has an invalid size_bytes value.")
        if len(self.sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.sha256
        ):
            raise ValueError("Update response has an invalid sha256 value.")

    def require_newer_than(self, current_version: str) -> None:
        if not self.update_available:
            return
        self.require_download_metadata()
        assert self.latest_version is not None
        if compare_versions(self.latest_version, current_version) <= 0:
            raise ValueError("Update response does not contain a newer version.")


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    width = max(len(left_parts), len(right_parts))
    left_parts += (0,) * (width - len(left_parts))
    right_parts += (0,) * (width - len(right_parts))
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def _version_parts(value: str) -> tuple[int, ...]:
    core = value.split("+", 1)[0].split("-", 1)[0]
    parts = tuple(int(part) for part in re.findall(r"\d+", core))
    if not parts:
        raise ValueError(f"Invalid version: {value}")
    return parts
