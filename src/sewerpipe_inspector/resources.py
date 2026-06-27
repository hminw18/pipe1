from __future__ import annotations

import sys
from pathlib import Path


def _dedupe_existing_order(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def app_root_candidates() -> list[Path]:
    package_dir = Path(__file__).resolve().parent
    candidates: list[Path] = []

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root))

    if getattr(sys, "frozen", False):
        executable = getattr(sys, "executable", None)
        if executable:
            candidates.append(Path(executable).resolve().parent)

    candidates.extend(
        [
            Path.cwd(),
            package_dir,
            package_dir / "assets",
        ]
    )
    if len(package_dir.parents) > 1:
        candidates.append(package_dir.parents[1])

    return _dedupe_existing_order(candidates)


def resource_path_candidates(relative_path: str | Path) -> list[Path]:
    relative = Path(relative_path)
    if relative.is_absolute():
        return [relative]
    return [root / relative for root in app_root_candidates()]


def find_resource(relative_path: str | Path) -> Path | None:
    for candidate in resource_path_candidates(relative_path):
        if candidate.exists():
            return candidate
    return None
