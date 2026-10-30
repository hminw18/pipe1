# syntax=docker/dockerfile:1.7
# Build from the repository root:
#   docker build -f server/Dockerfile --target api    -t pipe1-api .
#   docker build -f server/Dockerfile --target worker -t pipe1-worker .
# The API image has no ffmpeg: untrusted media is parsed only in the worker
# (docs/Web_Architecture.md §4).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
# Build the venv at its final path: scripts (alembic, uvicorn) hard-code it.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY core/ /app/core/
COPY server/pyproject.toml server/uv.lock server/README.md /app/server/
COPY server/src/ /app/server/src/
WORKDIR /app/server
# Locked dependencies only; no dev tools in the image.
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime-base
RUN groupadd --system app && useradd --system --gid app --home /app app \
 && mkdir -p /data/storage && chown app:app /data/storage
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Assets used by the PDF/Excel generators (pipe template, calibration, Korean fonts).
COPY pipe.png pipe_template_calibration.json /app/resources/
COPY font/ /app/resources/font/
# Depth counter text recognizer (models/ocr/README.md).
COPY models/ /app/resources/models/
COPY server/alembic.ini /app/alembic.ini
COPY server/migrations/ /app/migrations/
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PIPE1_RESOURCE_ROOT=/app/resources \
    PORT=8080
USER app

FROM runtime-base AS api
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn pipe1_server.main:app --host 0.0.0.0 --port ${PORT} --no-server-header"]

FROM runtime-base AS worker
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn pipe1_server.worker:app --host 0.0.0.0 --port ${PORT}"]
