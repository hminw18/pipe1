# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Pipe1 (`sewerpipe_inspector`) is an offline, single-user Windows desktop app (PySide6) for reviewing sewer CCTV videos, capturing defects, and exporting Excel/PDF inspection reports. Most UI strings, docs and domain terms are Korean.

The app is being migrated to a multi-user web app (FastAPI + React on GCP, email/password login); this desktop code is the starting point and stays shippable until the web app reaches parity. Docs:
- `docs/Product_Requirements.md`: platform-independent product spec (data model, report workspace, export rules, shortcuts)
- `docs/Defect_Taxonomy.md`: defect codes, condition items, grade scores
- `docs/Web_Architecture.md`: GCP target architecture, migration phases, desktop issues not to carry over
- `docs/Web_Auth_Spec.md`: login, sessions, roles, project visibility, authorization rules
- `docs/Testing.md`: what each test protects and the bar a new test must meet; update it when adding or removing tests

## Commands

```bash
pip install -e ".[dev]"                 # Python >= 3.11
python -m sewerpipe_inspector            # run the app (src layout; needs install or PYTHONPATH=src)
pytest                                   # all tests (pyproject sets pythonpath=src, testpaths=tests)
pytest tests/test_db.py::test_name       # single test
pyright                                  # type check (config in pyrightconfig.json, basic mode)
```

Repository layout: `src/sewerpipe_inspector/` (desktop, Qt), `core/` (`pipe1_core`: Qt-free defect taxonomy, grading, stop detection, Excel/PDF; used by both apps), `server/` (web API and worker), `web/` (React frontend), `infra/terraform/` (GCP). The desktop's pytest config covers `src` and `core/src`.

Web backend (`server/`, Python 3.12 via uv; details in `server/README.md`):

```bash
cd server && uv sync
uv run pytest                                   # DB tests need PIPE1_TEST_ADMIN_URL (Postgres superuser URL), else skipped
uv run pytest tests/test_row_level_security.py::test_name
uv run ruff check . && uv run ruff format --check .
uv run alembic check                            # models and migrations agree
```

Server essentials: the app connects as `pipe1_app` (not the table owner), and every transaction must call `pipe1_server.db.set_access_context()`; PostgreSQL row-level security on `inspection_projects` and all tables below it enforces the permission matrix in `docs/Web_Auth_Spec.md` §2. Every route loads its target through `security/authorization.authorize()`. Every table under a project carries `project_id`, tied to its parent by a composite foreign key. Generate migrations with `alembic revision --autogenerate`, then add RLS policies and grants for new tables by hand (see `migrations/versions/0001_initial_schema.py`). Use `db.after_commit()`, never FastAPI `BackgroundTasks`, for work that must follow the commit (jobs, emails, file deletes): background tasks run before the commit. Media (ffmpeg/OpenCV) runs only in the worker (`pipe1_server.worker`) in production.

Frontend (`web/`, Node 24): `npm ci`, `npm run dev` (proxies `/api` to localhost:8000), `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`. After changing the API, regenerate types with `npm run api:types`. The CSP forbids inline styles and scripts: set dynamic positions through `element.style`, never `style` attributes in HTML or `<style>` blocks.

Infrastructure (`infra/terraform/`): `terraform init -backend-config=envs/dev.backend.hcl`, then `terraform plan -var-file=envs/dev.tfvars`. `apply` creates billed GCP resources; ask before running it. CI and deploy workflows are in `.github/workflows/`.

Windows packaging (PowerShell): `scripts/build_windows.ps1` (PyInstaller via `packaging/pipe1_desktop.spec`, runs tests unless `-SkipTests`) and `scripts/build_msi.ps1` (WiX, `packaging/wix/*.wxs`). Builds require a production `config/pipe1.client.env` (`PIPE1_APP_ENV=prod`, https license API URL, non-empty public keys) unless `-UseExampleConfig`.

The version `0.1.2` is hardcoded in several places (pyproject.toml, `licensing/config.py` default, update/training services, UI, tests) — grep for it when bumping.

## Architecture

**Startup (`app.py:run`)**: `__main__` first checks whether it was launched as the packaged updater helper (`updates/installer_helper.py`). Otherwise: license activation gate → choose workspace folder (persisted via `settings_service.py`) → build `Database`, `StorageService`, `ReportService`, `TrainingUploadService`, `UpdateService` → `InspectionService` → `MainWindow`. License revalidation and update checks run in background `QThread` workers.

**Data model**: `Project → Business → Report`, where each Report has exactly one Video, pipe info / actual-survey info, manholes, and Defects. Old `zones/pipes` tables are intentionally ignored (no migration of old data). All persistence is in `db.py` (one large `Database` class over SQLite, `<workspace>/sewerpipe_inspector.db`); it opens a short-lived connection per call or uses `db.transaction()`, and schema migrations live in `_run_migrations_on_connection`. Field lists like `REPORT_FIELDS`, `MANHOLE_FIELDS`, `ACTUAL_SURVEY_FIELDS` drive both DB columns and UI forms.

**Workspace on disk**: `StorageService` lays out captures/report files under `<workspace>/<project>/<business_code>_<business_name>/...` with sanitized names. Logs go to `<workspace>/error.log`.

**Services (`services/`)**: `InspectionService` is the façade the UI calls for video registration, defect capture (frame → image file + DB row, optional training upload), and report export. `report_service.py` builds Excel workbooks (openpyxl); `pdf_report_service.py` builds the visual PDF with reportlab, drawing a pipe diagram from `pipe.png` calibrated by `pipe_template_calibration.json` and using bundled Pretendard fonts for Korean text. `ocr_service.py` wraps optional pytesseract.

**Stop detection (`stop_detection/`)**: finds where the camera stopped in a video (likely defect locations). `StopFrameCandidateDetector` does motion-based candidate detection, then `DepthOcrStopSegmentDetector` reads the on-screen depth/distance counter. With `onnxruntime` installed (the server, via `pipe1-core[ocr]`) it uses a pretrained text recognizer (`text_reader.py`, model in `models/ocr/`); otherwise (the desktop) it matches digit templates from `depth_template_data.py`, which only fit one recorder's `ddd.d` counter. `StopSegmentDetector` is the simpler motion-only detector. Invoked from `MainWindow` on a worker thread.

**Domain rules**: `defect_taxonomy.py` (defect codes) and `state_grading.py` (condition grades) back both UI choices and report output.

**UI (`ui/`)**: `main_window.py` is very large (~8.5k lines) and holds most UI logic; read targeted sections rather than the whole file.

**Licensing (`licensing/`)**: online activation against `PIPE1_LICENSE_API_BASE_URL`, signed entitlements verified with `PIPE1_LICENSE_PUBLIC_KEYS`, local state protected with Windows DPAPI. Activation is required only when `PIPE1_APP_ENV` is prod/production (or `PIPE1_REQUIRE_LICENSE_ACTIVATION=true`); in `dev` the app runs without a license. Runtime config is read from env vars layered over `config/pipe1.client.env` (or `PIPE1_CLIENT_ENV_FILE`), falling back to the `.example` file. `updates/` checks for and installs new MSI versions using the same config.

**Resources**: always locate bundled assets (fonts, `pipe.png`, `logo.png`, config) via `resources.find_resource`, which searches the PyInstaller `_MEIPASS` dir, the executable dir, cwd and the repo root. New assets must also be added to `_datas()` in `packaging/pipe1_desktop.spec` (and to the WiX sources if the installer needs them); `tests/test_packaging_resources.py` covers resource lookup and packaging.
