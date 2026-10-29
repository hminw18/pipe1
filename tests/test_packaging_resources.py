from __future__ import annotations

import sys
from pathlib import Path

from pipe1_core import resources


def test_find_resource_checks_pyinstaller_bundle_root(
    tmp_path: Path, monkeypatch
) -> None:
    bundle_root = tmp_path / "_internal"
    bundle_root.mkdir()
    pipe_png = bundle_root / "pipe.png"
    pipe_png.write_bytes(b"png")

    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert resources.find_resource("pipe.png") == pipe_png
