from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_CSRF_KEY = "dev-only-csrf-key-do-not-use-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPE1_", env_file=".env", extra="ignore")

    environment: str = "dev"

    # Runtime connection. Must use the non-owner app role so row-level security applies.
    database_url: str = "postgresql+psycopg://pipe1_app@localhost:5432/pipe1"
    # Connection used by Alembic. Must use the role that owns the tables.
    migration_database_url: str = "postgresql+psycopg://pipe1_migrator@localhost:5432/pipe1"

    # Exact origin of the web app, e.g. https://app.example.com. State-changing requests
    # must carry this Origin header (docs/Web_Auth_Spec.md §7).
    app_origin: str = "http://localhost:5173"
    # Number of trusted reverse proxies that append to X-Forwarded-For (load balancer).
    # 0 means use the socket peer address.
    trusted_proxy_hops: int = 0

    csrf_key: SecretStr = SecretStr(DEV_CSRF_KEY)
    # Base64 32-byte key for the local TOTP secret cipher (dev/test). Production uses Cloud KMS.
    totp_local_key: SecretStr | None = None
    totp_kms_key: str | None = None
    totp_issuer: str = "Pipe1"

    session_idle_timeout_seconds: int = 7200
    session_absolute_timeout_seconds: int = 43200
    reauthentication_window_seconds: int = 600
    login_challenge_ttl_seconds: int = 300
    login_challenge_max_attempts: int = 5

    # Email (docs/Web_Auth_Spec.md §11). Queue: "inline" sends in the caller's thread (tests),
    # "background" sends from a thread in this process (single-host dev VM), "cloud_tasks" hands
    # off to the mailer (production). Sender: "console" logs (dev), "resend" sends.
    email_queue: str = "inline"
    email_sender: str = "console"
    email_from: str = "Pipe1 <no-reply@localhost>"
    resend_api_key: SecretStr | None = None
    # Cloud Tasks: full queue name, worker endpoint URL, and the service account whose
    # OIDC token the worker accepts.
    cloud_tasks_queue: str | None = None
    worker_url: str | None = None
    tasks_service_account: str | None = None
    # Email tasks go to a separate mailer service with internet egress (Resend); the media
    # worker has none. Falls back to worker_url.
    mailer_url: str | None = None

    # Background jobs and media: "inline" runs in the caller's thread (tests), "background" runs
    # on worker threads in the API process (single-host dev VM); "cloud_tasks" / "worker" use the
    # isolated worker service (production).
    job_queue: str = "inline"
    media_client: str = "local"
    # Service account of the API; the worker accepts synchronous media calls only from it.
    api_service_account: str | None = None

    # Object storage: "gcs" (production) or "local" (development and tests).
    storage_backend: str = "local"
    media_bucket: str | None = None
    # Service account that signs URLs on Cloud Run (no private key available there).
    storage_signer_email: str | None = None
    local_storage_dir: str = ".local-storage"
    # Origin that serves /api for local signed URLs; defaults to app_origin.
    public_api_origin: str | None = None
    # Upload limits (docs/Web_Architecture.md §4).
    max_video_bytes: int = 20 * 1024**3
    organization_storage_quota_bytes: int = 2 * 1024**4

    # AI defect detection on the separate GPU server (pipe1_server.detection). Unset: the
    # feature is off. A sample result file stands in for the server in development.
    defect_detector_url: str | None = None
    defect_detector_token: SecretStr | None = None
    defect_detector_sample_file: str | None = None

    # Breached-password corpus (docs/Web_Auth_Spec.md §4): "hibp" or "offline".
    breached_password_source: str = "offline"
    breached_password_file: str | None = None

    @model_validator(mode="after")
    def _production_requires_real_secrets(self) -> Settings:
        if self.environment != "dev":
            if self.csrf_key.get_secret_value() == DEV_CSRF_KEY:
                raise ValueError("PIPE1_CSRF_KEY must be set outside dev")
            if not self.app_origin.startswith("https://"):
                raise ValueError("PIPE1_APP_ORIGIN must be https:// outside dev")
            if not self.totp_kms_key:
                raise ValueError("PIPE1_TOTP_KMS_KEY must be set outside dev")
            if self.breached_password_source == "offline" and not self.breached_password_file:
                raise ValueError("PIPE1_BREACHED_PASSWORD_FILE must be set outside dev")
            if self.storage_backend != "gcs" or not self.media_bucket:
                raise ValueError("outside dev, storage must be gcs with PIPE1_MEDIA_BUCKET")
            if self.job_queue != "cloud_tasks" or self.media_client != "worker":
                # Media parsing must stay in the isolated worker (docs/Web_Architecture.md §4).
                raise ValueError("outside dev, jobs and media must run in the worker")
            if not self.api_service_account:
                raise ValueError("PIPE1_API_SERVICE_ACCOUNT is required outside dev")
            if self.email_queue != "cloud_tasks" or self.email_sender != "resend":
                raise ValueError("outside dev, email must use cloud_tasks and resend")
            if not (self.cloud_tasks_queue and self.worker_url and self.tasks_service_account):
                raise ValueError("Cloud Tasks queue, worker URL and service account are required")
        if self.job_queue not in ("inline", "background", "cloud_tasks"):
            raise ValueError("PIPE1_JOB_QUEUE must be inline, background or cloud_tasks")
        if self.email_queue not in ("inline", "background", "cloud_tasks"):
            raise ValueError("PIPE1_EMAIL_QUEUE must be inline, background or cloud_tasks")
        if self.email_sender not in ("console", "resend"):
            raise ValueError("PIPE1_EMAIL_SENDER must be console or resend")
        if self.email_sender == "resend":
            if self.resend_api_key is None or not self.resend_api_key.get_secret_value():
                raise ValueError("PIPE1_RESEND_API_KEY is required when sending with Resend")
            if self.email_from.rstrip(">").endswith("@localhost"):
                raise ValueError("PIPE1_EMAIL_FROM must be an address on a verified domain")
        if self.breached_password_source not in ("hibp", "offline"):
            raise ValueError("PIPE1_BREACHED_PASSWORD_SOURCE must be hibp or offline")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
