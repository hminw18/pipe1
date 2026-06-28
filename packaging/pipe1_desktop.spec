# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(os.environ.get("PIPE1_PROJECT_ROOT", Path.cwd())).resolve()
CLIENT_ENV_FILE = Path(
    os.environ.get(
        "PIPE1_BUILD_CLIENT_ENV_FILE",
        str(ROOT / "config" / "pipe1.client.env"),
    )
).resolve()


def _datas() -> list[tuple[str, str]]:
    datas: list[tuple[str, str]] = []

    required_paths = [
        (ROOT / "font", "font"),
        (ROOT / "pipe.png", "."),
        (ROOT / "logo.png", "."),
        (ROOT / "pipe_template_calibration.json", "."),
    ]
    for source, destination in required_paths:
        if not source.exists():
            raise FileNotFoundError(f"Required bundled resource is missing: {source}")
        datas.append((str(source), destination))

    if CLIENT_ENV_FILE.exists():
        datas.append((str(CLIENT_ENV_FILE), "config"))
    else:
        example_env = ROOT / "config" / "pipe1.client.env.example"
        if not example_env.exists():
            raise FileNotFoundError(
                f"Client env file is missing: {CLIENT_ENV_FILE}"
            )
        datas.append((str(example_env), "config"))

    return datas


a = Analysis(
    [str(ROOT / "packaging" / "pyinstaller_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=_datas(),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PIPE1",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "assets" / "pipe1.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PIPE1",
)
