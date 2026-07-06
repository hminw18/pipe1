from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

from sewerpipe_inspector.updates.api_client import (
    UpdateApiClientProtocol,
    UpdateConnectionError,
)
from sewerpipe_inspector.updates.models import UpdateInfo


HELPER_FLAG = "--pipe1-apply-update"


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
        info = self.client.latest_release(
            platform=self.platform,
            arch=self.arch,
            channel=self.channel,
            current_version=self.current_version,
        )
        try:
            info.require_newer_than(self.current_version)
        except ValueError as exc:
            raise UpdateConnectionError(str(exc)) from exc
        return info

    def download_update(self, info: UpdateInfo) -> Path:
        info.require_newer_than(self.current_version)
        assert info.download_url is not None
        assert info.sha256 is not None
        assert info.size_bytes is not None
        self._validate_download_url(info.download_url)
        filename = self._download_filename(info)
        target_path = self.download_dir / filename
        temp_path = target_path.with_name(f"{target_path.name}.download")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.stream(
                "GET",
                info.download_url,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                self._validate_response_size(response, info.size_bytes)
                with temp_path.open("wb") as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        if temp_path.stat().st_size != info.size_bytes:
            temp_path.unlink(missing_ok=True)
            raise UpdateSecurityError("Downloaded update size does not match.")
        if self.sha256(temp_path) != info.sha256:
            temp_path.unlink(missing_ok=True)
            raise UpdateSecurityError("Downloaded update sha256 does not match.")
        temp_path.replace(target_path)
        return target_path

    def verify_sha256(self, path: Path, expected_sha256: str) -> None:
        if self.sha256(path) != expected_sha256:
            raise UpdateSecurityError("Downloaded update sha256 does not match.")

    def launch_installer(
        self,
        msi_path: Path,
        *,
        wait_for_pid: int | None = None,
    ) -> subprocess.Popen:
        if not msi_path.exists() or msi_path.suffix.lower() != ".msi":
            raise FileNotFoundError(str(msi_path))
        command = self._installer_helper_command(msi_path, wait_for_pid)
        popen_kwargs: dict[str, object] = {
            "close_fds": True,
            "env": self._installer_helper_env(),
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(command, **popen_kwargs)

    def installer_log_path(self, msi_path: Path) -> Path:
        return msi_path.with_name(f"{msi_path.stem}.install.log")

    def installer_result_path(self, msi_path: Path) -> Path:
        return msi_path.with_name(f"{msi_path.stem}.install-result.json")

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
            raise UpdateSecurityError("Update download URL must use HTTPS.")
        if not parsed.hostname or parsed.hostname.lower() != self.allowed_download_host:
            raise UpdateSecurityError("Update download host is not allowed.")

    @staticmethod
    def _validate_response_size(response: httpx.Response, expected_size: int) -> None:
        content_length = response.headers.get("Content-Length")
        if not content_length:
            return
        try:
            actual_size = int(content_length)
        except ValueError:
            return
        if actual_size != expected_size:
            raise UpdateSecurityError("Update response size does not match.")

    @staticmethod
    def _download_filename(info: UpdateInfo) -> str:
        if info.latest_version:
            return f"PIPE1-{info.latest_version}.msi"
        parsed = urlparse(info.download_url or "")
        name = Path(parsed.path).name
        return name if name.lower().endswith(".msi") else "PIPE1-update.msi"

    def _installer_helper_command(
        self,
        msi_path: Path,
        wait_for_pid: int | None,
    ) -> list[str]:
        if getattr(sys, "frozen", False):
            command = [sys.executable, HELPER_FLAG]
        else:
            command = [
                sys.executable,
                "-m",
                "sewerpipe_inspector.updates.installer_helper",
            ]
        command.extend(
            [
                "--msi",
                str(msi_path),
                "--log",
                str(self.installer_log_path(msi_path)),
                "--result",
                str(self.installer_result_path(msi_path)),
                "--show-failure-dialog",
            ]
        )
        if wait_for_pid is not None and wait_for_pid > 0:
            command.extend(["--wait-pid", str(wait_for_pid)])
        return command

    @staticmethod
    def _installer_helper_env() -> dict[str, str]:
        env = os.environ.copy()
        if getattr(sys, "frozen", False):
            return env
        source_root = Path(__file__).resolve().parents[2]
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{source_root}{os.pathsep}{existing}" if existing else str(source_root)
        )
        return env
