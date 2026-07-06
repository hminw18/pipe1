from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


SETTINGS_PATH = Path.home() / ".sewerpipe_inspector_settings.json"


@dataclass
class AppSettings:
    default_workspace: str | None = None
    suppress_default_workspace_prompt: bool = False
    always_show_directory_picker: bool = False
    report_view_scale: float = 1.0
    developer_mode: bool = False
    suppressed_update_prompt_version: str | None = None


def load_settings() -> AppSettings:
    if not SETTINGS_PATH.exists() or not SETTINGS_PATH.is_file():
        return AppSettings()
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return AppSettings()

    default_workspace = raw.get("default_workspace")
    if not isinstance(default_workspace, str):
        default_workspace = None
    suppress_prompt = bool(raw.get("suppress_default_workspace_prompt", False))
    if "suppress_default_workspace_prompt" not in raw:
        suppress_prompt = bool(raw.get("auto_load_default_workspace", False))
    always_show_picker = bool(raw.get("always_show_directory_picker", False))
    try:
        report_view_scale = float(raw.get("report_view_scale", 1.0))
    except (TypeError, ValueError):
        report_view_scale = 1.0
    if not 0.8 <= report_view_scale <= 1.2:
        report_view_scale = 1.0
    developer_mode = bool(raw.get("developer_mode", False))
    suppressed_update_prompt_version = raw.get("suppressed_update_prompt_version")
    if not isinstance(suppressed_update_prompt_version, str):
        suppressed_update_prompt_version = None
    return AppSettings(
        default_workspace=default_workspace,
        suppress_default_workspace_prompt=suppress_prompt,
        always_show_directory_picker=always_show_picker,
        report_view_scale=report_view_scale,
        developer_mode=developer_mode,
        suppressed_update_prompt_version=suppressed_update_prompt_version,
    )


def save_settings(settings: AppSettings) -> None:
    payload = {
        "default_workspace": settings.default_workspace,
        "suppress_default_workspace_prompt": settings.suppress_default_workspace_prompt,
        "always_show_directory_picker": settings.always_show_directory_picker,
        "report_view_scale": settings.report_view_scale,
        "developer_mode": settings.developer_mode,
        "suppressed_update_prompt_version": settings.suppressed_update_prompt_version,
    }
    SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
