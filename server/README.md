# Pipe1 server

FastAPI backend for the Pipe1 web app. Design: `docs/Web_Architecture.md`, `docs/Web_Auth_Spec.md`.

## Setup

```bash
cd server
uv sync                      # Python 3.12 venv + dependencies
cp .env.example .env
docker compose up -d         # local PostgreSQL 17 with pipe1_migrator / pipe1_app roles
uv run alembic upgrade head  # migrate as pipe1_migrator
uv run uvicorn pipe1_server.main:app --reload
```

## Tests and checks

```bash
uv run pytest                                   # DB tests need PIPE1_TEST_ADMIN_URL, else skipped
uv run pytest tests/test_row_level_security.py  # one file
uv run ruff check . && uv run ruff format --check .
uv run alembic check                            # models and migrations agree
```

The test suite creates a throwaway database per run, migrates it, and runs assertions as `pipe1_app`.

## Services and images

One Dockerfile (`server/Dockerfile`, built from the repository root) with two targets:

| Target | Runs | Notes |
| --- | --- | --- |
| `api` | `pipe1_server.main:app` | No ffmpeg; never parses media. |
| `worker` | `pipe1_server.worker:app` | ffmpeg; runs jobs (`/internal/tasks/job`), email (`/internal/tasks/email`) and frame capture for the API (`/internal/media/capture`). Deployed twice: `worker` (no internet) and `mailer` (SendGrid only). |

In development everything runs in the API process: `PIPE1_JOB_QUEUE=inline`, `PIPE1_MEDIA_CLIENT=local`, `PIPE1_EMAIL_QUEUE=inline`, `PIPE1_EMAIL_SENDER=console` (links are logged), `PIPE1_STORAGE_BACKEND=local`. Production settings refuse to start unless these point at Cloud Tasks, the worker, SendGrid and Cloud Storage. Bootstrap the first platform admin with `uv run python -m pipe1_server.cli invite-platform-admin <email>`.

## Database roles

| Role | Used by | Notes |
| --- | --- | --- |
| `pipe1_migrator` | Alembic | Owns all tables. Row-level security does not apply to it, so never use it at runtime. |
| `pipe1_app` | The API and workers | Gets only the grants in the migrations; row-level security applies. |

## Access control in the database

Every request runs in one transaction that starts with `pipe1_server.db.set_access_context()` (organization, user, role). Row-level security policies on `inspection_projects` and every table below it enforce the permission matrix from `docs/Web_Auth_Spec.md` §2. Without a context, the app role sees no project data. Application-level authorization checks are still required; the policies are the backstop.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"   # then review the generated file
uv run alembic upgrade head
```

New tables below `inspection_projects` need a `project_id` column, a composite foreign key to their parent, a row-level security policy, and grants for `pipe1_app` (see `migrations/versions/0001_initial_schema.py`).
