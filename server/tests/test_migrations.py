from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from pipe1_server.models import Base


def test_migrated_schema_matches_models(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_row_level_security_enabled_on_all_project_tables(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        protected = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND rowsecurity")
            ).scalars()
        )
    assert protected == {
        "inspection_projects",
        "businesses",
        "report_groups",
        "reports",
        "manholes",
        "pipe_information",
        "actual_survey_information",
        "report_videos",
        "report_defects",
        "video_uploads",
        "media_jobs",
        "defect_suggestions",
    }


def test_app_role_cannot_modify_audit_history(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        privileges = set(
            conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants"
                    " WHERE grantee = current_user AND table_name = 'audit_events'"
                )
            ).scalars()
        )
    assert privileges == {"SELECT", "INSERT"}


def test_downgrade_and_upgrade_round_trip(
    database_urls: dict[str, URL], migrator_alembic_config: Config
) -> None:
    config = migrator_alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_engine(database_urls["migrator"])
    try:
        with engine.connect() as conn:
            remaining = conn.execute(
                text(
                    "SELECT count(*) FROM pg_tables"
                    " WHERE schemaname = 'public' AND tablename != 'alembic_version'"
                )
            ).scalar()
        assert remaining == 0
    finally:
        engine.dispose()
    command.upgrade(config, "head")
