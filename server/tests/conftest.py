"""Database fixtures.

Database tests need a PostgreSQL superuser connection in PIPE1_TEST_ADMIN_URL, e.g.
postgresql+psycopg://postgres@localhost:5432/postgres. Each test session creates a fresh
database, migrates it as pipe1_migrator, and connects the code under test as pipe1_app.
Without the variable, database tests are skipped.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.engine import URL

SERVER_ROOT = Path(__file__).resolve().parents[1]
MIGRATOR_ROLE = "pipe1_migrator"
APP_ROLE = "pipe1_app"


def _admin_url() -> URL:
    raw = os.environ.get("PIPE1_TEST_ADMIN_URL")
    if not raw:
        pytest.skip("PIPE1_TEST_ADMIN_URL not set; skipping database tests")
    return make_url(raw)


def _alembic_config(url: URL) -> Config:
    config = Config(str(SERVER_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    return config


@pytest.fixture(scope="session")
def database_urls() -> Iterator[dict[str, URL]]:
    """Create an empty test database; yield admin/migrator/app URLs for it."""
    admin_url = _admin_url()
    name = f"pipe1_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        for role in (MIGRATOR_ROLE, APP_ROLE):
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
            ).scalar()
            if not exists:
                conn.execute(text(f"CREATE ROLE {role} LOGIN"))
        conn.execute(text(f"CREATE DATABASE {name} OWNER {MIGRATOR_ROLE}"))
    urls = {
        "admin": admin_url.set(database=name),
        "migrator": admin_url.set(database=name, username=MIGRATOR_ROLE, password=None),
        "app": admin_url.set(database=name, username=APP_ROLE, password=None),
    }
    try:
        yield urls
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


@pytest.fixture(scope="session")
def migrated(database_urls: dict[str, URL]) -> dict[str, URL]:
    command.upgrade(_alembic_config(database_urls["migrator"]), "head")
    return database_urls


@pytest.fixture(scope="session")
def migrator_alembic_config(database_urls: dict[str, URL]) -> Config:
    return _alembic_config(database_urls["migrator"])


@pytest.fixture(scope="session")
def owner_engine(migrated: dict[str, URL]) -> Iterator[Engine]:
    """Table owner: row-level security does not apply. Use only for test setup."""
    engine = create_engine(migrated["migrator"])
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(migrated: dict[str, URL]) -> Iterator[Engine]:
    """Runtime role, subject to row-level security."""
    engine = create_engine(migrated["app"], pool_size=5, max_overflow=5)
    yield engine
    engine.dispose()


@pytest.fixture
def clock():
    from tests.api_support import Clock

    return Clock()


@pytest.fixture
def outbox():
    from pipe1_server.email.delivery import InMemoryQueue

    return InMemoryQueue()


@pytest.fixture
def client(app_engine: Engine, clock, outbox):
    from tests.api_support import make_client, make_settings

    with make_client(app_engine, clock, make_settings(), outbox) as test_client:
        yield test_client
