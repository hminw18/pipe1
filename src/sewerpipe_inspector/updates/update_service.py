from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from sewerpipe_inspector.updates.api_client import (
    UpdateApiClientProtocol,
    UpdateConnectionError,
)
from sewerpipe_inspector.updates.models import UpdateInfo


class UpdateSecurityError(RuntimeError):
    pass


class UpdateService:
    def __init__(
        self,
        *,
        client: UpdateApiClientProtocol,
        current_version: str,
        platform: str,
        arch: str,
        channel: str,
        download_dir: Path,
        allowed_download_host: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.client = client
        self.current_version = current_version
        self.platform = platform
        self.arch = arch
        self.channel = channel
        self.download_dir = download_dir
        self.allowed_download_host = allowed_download_host.lower()
        self.timeout_seconds = timeout_seconds

    def check_for_update(self) -> UpdateInfo:
        return self.client.latest_release(
            platform=self.platform,
            arch=self.arch,
            channel=self.channel,
            current_version=self.current_version,
        )

    def download_update(self, info: UpdateInfo) -> Path:
        info.require_download_metadata()
        assert info.download_url is not None
        assert info.sha256 is not None
        self._validate_download_url(info.download_url)
        filename = self._download_filename(info)
        target_path = self.download_dir / filename
        self.download_dir.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.stream(
                "GET",
                info.download_url,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                with target_path.open("wb") as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
        except Exception:
            target_path.unlink(missing_ok=True)
            raise
        if self.sha256(target_path) != info.sha256:
            target_path.unlink(missing_ok=True)
            raise UpdateSecurityError("업데이트 파일 해시가 일치하지 않습니다.")
        return target_path

    def verify_sha256(self, path: Path, expected_sha256: str) -> None:
        if self.sha256(path) != expected_sha256:
            raise UpdateSecurityError("업데이트 파일 해시가 일치하지 않습니다.")

    def launch_installer(self, msi_path: Path) -> subprocess.Popen:
        if not msi_path.exists() or msi_path.suffix.lower() != ".msi":
            raise FileNotFoundError(str(msi_path))
        return subprocess.Popen(
            [
                "msiexec.exe",
                "/i",
                str(msi_path),
                "/quiet",
                "/norestart",
            ],
            close_fds=True,
        )

    @staticmethod
    def default_download_dir() -> Path:
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "Pipe1" / "updates"
        return Path.home() / ".pipe1" / "updates"

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _validate_download_url(self, download_url: str) -> None:
        parsed = urlparse(download_url)
        if parsed.scheme != "https":
            raise UpdateSecurityError("업데이트 다운로드 URL은 HTTPS여야 합니다.")
        if not parsed.hostname or parsed.hostname.lower() != self.allowed_download_host:
            raise UpdateSecurityError("허용되지 않은 업데이트 다운로드 도메인입니다.")

    @staticmethod
    def _download_filename(info: UpdateInfo) -> str:
        if info.latest_version:
            return f"PIPE1-{info.latest_version}.msi"
        parsed = urlparse(info.download_url or "")
        name = Path(parsed.path).name
        return name if name.lower().endswith(".msi") else "PIPE1-update.msi"
