from __future__ import annotations

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
        if not self.download_url or not self.sha256:
            raise ValueError("업데이트 다운로드 정보가 올바르지 않습니다.")
        if len(self.sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.sha256
        ):
            raise ValueError("업데이트 해시 형식이 올바르지 않습니다.")


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
