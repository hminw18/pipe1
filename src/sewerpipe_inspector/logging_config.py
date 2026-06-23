from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path),
        level=logging.ERROR,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
