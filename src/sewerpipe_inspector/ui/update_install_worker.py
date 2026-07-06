from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from sewerpipe_inspector.updates.models import UpdateInfo
from sewerpipe_inspector.updates.update_service import UpdateService


class UpdateInstallWorker(QObject):
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, service: UpdateService, info: UpdateInfo) -> None:
        super().__init__()
        self.service = service
        self.info = info

    @Slot()
    def run(self) -> None:
        try:
            msi_path = self.service.download_update(self.info)
            self.service.launch_installer(msi_path, wait_for_pid=os.getpid())
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.succeeded.emit(str(msi_path))
        finally:
            self.finished.emit()


def update_install_log_path(service: UpdateService, msi_path: str) -> Path:
    return service.installer_log_path(Path(msi_path))
