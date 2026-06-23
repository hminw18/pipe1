from __future__ import annotations

import sys
from pathlib import Path


APP_FONT_FAMILY = "Pretendard"
APP_FONT_DIRNAME = "font"
APP_FONT_FILENAMES = [
    "Pretendard-Thin.ttf",
    "Pretendard-ExtraLight.ttf",
    "Pretendard-Light.ttf",
    "Pretendard-Regular.ttf",
    "Pretendard-Medium.ttf",
    "Pretendard-SemiBold.ttf",
    "Pretendard-Bold.ttf",
    "Pretendard-ExtraBold.ttf",
    "Pretendard-Black.ttf",
]
APP_FONT_REGULAR_FILENAME = "Pretendard-Regular.ttf"
APP_FONT_BOLD_FILENAME = "Pretendard-Bold.ttf"


def app_font_dir_candidates() -> list[Path]:
    package_dir = Path(__file__).resolve().parent
    candidates = [
        package_dir / "assets" / "fonts",
        Path.cwd() / APP_FONT_DIRNAME,
        package_dir.parents[1] / APP_FONT_DIRNAME,
    ]

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / APP_FONT_DIRNAME)
        candidates.append(Path(bundle_root) / "assets" / "fonts")

    return candidates


def find_app_font_dir() -> Path | None:
    for candidate in app_font_dir_candidates():
        if (
            candidate.exists()
            and candidate.is_dir()
            and (candidate / APP_FONT_REGULAR_FILENAME).exists()
        ):
            return candidate
    return None


def find_app_font_paths() -> list[Path]:
    font_dir = find_app_font_dir()
    if font_dir is None:
        return []
    return [
        font_dir / filename
        for filename in APP_FONT_FILENAMES
        if (font_dir / filename).exists()
    ]


def find_app_report_font_paths() -> tuple[Path, Path] | None:
    font_dir = find_app_font_dir()
    if font_dir is None:
        return None

    regular_path = font_dir / APP_FONT_REGULAR_FILENAME
    bold_path = font_dir / APP_FONT_BOLD_FILENAME
    if regular_path.exists() and bold_path.exists():
        return regular_path, bold_path
    if regular_path.exists():
        return regular_path, regular_path
    return None
