from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sewerpipe_inspector.updates.models import UpdateInfo
from sewerpipe_inspector.updates.update_service import UpdateSecurityError, UpdateService


class FakeUpdateClient:
    def __init__(self, info: UpdateInfo) -> None:
        self.info = info

    def latest_release(
        self,
        *,
        platform: str,
        arch: str,
        channel: str,
        current_version: str,
    ) -> UpdateInfo:
        return self.info


def _update_info(*, url: str, sha256: str) -> UpdateInfo:
    return UpdateInfo(
        update_available=True,
        current_version="0.1.0",
        latest_version="0.1.1",
        mandatory=False,
        min_supported_version=None,
        download_url=url,
        sha256=sha256,
        size_bytes=3,
        release_notes="bug fixes",
        published_at="2026-06-29T00:00:00Z",
    )


def _service(tmp_path: Path, info: UpdateInfo) -> UpdateService:
    return UpdateService(
        client=FakeUpdateClient(info),
        current_version="0.1.0",
        platform="windows",
        arch="x64",
        channel="stable",
        download_dir=tmp_path,
        allowed_download_host="pipe1dev.cloud",
    )


def test_update_service_rejects_download_from_other_host(tmp_path: Path) -> None:
    payload_hash = hashlib.sha256(b"msi").hexdigest()
    service = _service(
        tmp_path,
        _update_info(url="https://evil.example.com/PIPE1-0.1.1.msi", sha256=payload_hash),
    )

    with pytest.raises(UpdateSecurityError):
        service.download_update(service.check_for_update())


def test_update_service_rejects_mismatched_sha256(tmp_path: Path) -> None:
    path = tmp_path / "PIPE1-0.1.1.msi"
    path.write_bytes(b"msi")
    service = _service(
        tmp_path,
        _update_info(
            url="https://pipe1dev.cloud/downloads/PIPE1-0.1.1.msi",
            sha256="0" * 64,
        ),
    )

    with pytest.raises(UpdateSecurityError):
        service.verify_sha256(path, "0" * 64)


def test_update_service_launches_quiet_msiexec(monkeypatch, tmp_path: Path) -> None:
    msi_path = tmp_path / "PIPE1-0.1.1.msi"
    msi_path.write_bytes(b"msi")
    service = _service(
        tmp_path,
        _update_info(
            url="https://pipe1dev.cloud/downloads/PIPE1-0.1.1.msi",
            sha256=hashlib.sha256(b"msi").hexdigest(),
        ),
    )
    calls: list[list[str]] = []

    class FakeProcess:
        pass

    def fake_popen(args: list[str], *, close_fds: bool) -> FakeProcess:
        calls.append(args)
        assert close_fds is True
        return FakeProcess()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    process = service.launch_installer(msi_path)

    assert isinstance(process, FakeProcess)
    assert calls == [
        [
            "msiexec.exe",
            "/i",
            str(msi_path),
            "/quiet",
            "/norestart",
        ]
    ]
