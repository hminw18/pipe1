# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Pipe1 (`sewerpipe_inspector`) is an offline, single-user Windows desktop app (PySide6) for reviewing sewer CCTV videos, capturing defects, and exporting Excel/PDF inspection reports. Most UI strings, docs and domain terms are Korean.

The app is being migrated to a multi-user web app (FastAPI + React on GCP, email/password login); this desktop code is the starting point and stays shippable until the web app reaches parity. Docs:
- `docs/Product_Requirements.md`: platform-independent product spec (data model, report workspace, export rules, shortcuts)
- `docs/Defect_Taxonomy.md`: defect codes, condition items, grade scores
- `docs/Web_Architecture.md`: GCP target architecture, migration phases, desktop issues not to carry over
- `docs/Web_Auth_Spec.md`: login, sessions, roles, project visibility, authorization rules

## Commands

```bash
pip install -e ".[dev]"                 # Python >= 3.11
python -m sewerpipe_inspector            # run the app (src layout; needs install or PYTHONPATH=src)
pytest                                   # all tests (pyproject sets pythonpath=src, testpaths=tests)
pytest tests/test_db.py::test_name       # single test
pyright                                  # type check (config in pyrightconfig.json, basic mode)
```

Windows packaging (PowerShell): `scripts/build_windows.ps1` (PyInstaller via `packaging/pipe1_desktop.spec`, runs tests unless `-SkipTests`) and `scripts/build_msi.ps1` (WiX, `packaging/wix/*.wxs`). Builds require a production `config/pipe1.client.env` (`PIPE1_APP_ENV=prod`, https license API URL, non-empty public keys) unless `-UseExampleConfig`.

The version `0.1.2` is hardcoded in several places (pyproject.toml, `licensing/config.py` default, update/training services, UI, tests) — grep for it when bumping.

## Architecture

**Startup (`app.py:run`)**: `__main__` first checks whether it was launched as the packaged updater helper (`updates/installer_helper.py`). Otherwise: license activation gate → choose workspace folder (persisted via `settings_service.py`) → build `Database`, `StorageService`, `ReportService`, `TrainingUploadService`, `UpdateService` → `InspectionService` → `MainWindow`. License revalidation and update checks run in background `QThread` workers.

**Data model**: `Project → Business → Report`, where each Report has exactly one Video, pipe info / actual-survey info, manholes, and Defects. Old `zones/pipes` tables are intentionally ignored (no migration of old data). All persistence is in `db.py` (one large `Database` class over SQLite, `<workspace>/sewerpipe_inspector.db`); it opens a short-lived connection per call or uses `db.transaction()`, and schema migrations live in `_run_migrations_on_connection`. Field lists like `REPORT_FIELDS`, `MANHOLE_FIELDS`, `ACTUAL_SURVEY_FIELDS` drive both DB columns and UI forms.

**Workspace on disk**: `StorageService` lays out captures/report files under `<workspace>/<project>/<business_code>_<business_name>/...` with sanitized names. Logs go to `<workspace>/error.log`.

**Services (`services/`)**: `InspectionService` is the façade the UI calls for video registration, defect capture (frame → image file + DB row, optional training upload), and report export. `report_service.py` builds Excel workbooks (openpyxl); `pdf_report_service.py` builds the visual PDF with reportlab, drawing a pipe diagram from `pipe.png` calibrated by `pipe_template_calibration.json` and using bundled Pretendard fonts for Korean text. `ocr_service.py` wraps optional pytesseract.

**Stop detection (`stop_detection/`)**: finds where the camera stopped in a video (likely defect locations). `StopFrameCandidateDetector` does motion-based candidate detection, then `DepthOcrStopSegmentDetector` reads the on-screen depth/distance counter via template matching against digit templates in `depth_template_data.py` (not Tesseract). `StopSegmentDetector` is the simpler motion-only detector. Invoked from `MainWindow` on a worker thread.

**Domain rules**: `defect_taxonomy.py` (defect codes) and `state_grading.py` (condition grades) back both UI choices and report output.

**UI (`ui/`)**: `main_window.py` is very large (~8.5k lines) and holds most UI logic; read targeted sections rather than the whole file.

**Licensing (`licensing/`)**: online activation against `PIPE1_LICENSE_API_BASE_URL`, signed entitlements verified with `PIPE1_LICENSE_PUBLIC_KEYS`, local state protected with Windows DPAPI. Activation is required only when `PIPE1_APP_ENV` is prod/production (or `PIPE1_REQUIRE_LICENSE_ACTIVATION=true`); in `dev` the app runs without a license. Runtime config is read from env vars layered over `config/pipe1.client.env` (or `PIPE1_CLIENT_ENV_FILE`), falling back to the `.example` file. `updates/` checks for and installs new MSI versions using the same config.

**Resources**: always locate bundled assets (fonts, `pipe.png`, `logo.png`, config) via `resources.find_resource`, which searches the PyInstaller `_MEIPASS` dir, the executable dir, cwd and the repo root. New assets must also be added to `_datas()` in `packaging/pipe1_desktop.spec` (and to the WiX sources if the installer needs them); `tests/test_packaging_resources.py` covers resource lookup and packaging.
