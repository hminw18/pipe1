"""local-first video: uploading status, resuming interrupted jobs

A report gets its video row when the upload starts ('uploading'), so defects can be captured
from the local file in the browser while the upload runs. Single-host deployments resume jobs
that a restart interrupted.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28 00:00:00.000000

"""

import re
from collections.abc import Sequence

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "'uploading', 'pending', 'processing', 'ready', 'failed'"
OLD_STATUSES = "'pending', 'processing', 'ready', 'failed'"


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("app_role", "pipe1_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"invalid app_role: {role!r}")
    return role


def upgrade() -> None:
    op.drop_constraint(op.f("ck_report_videos_transcode_status"), "report_videos", type_="check")
    op.create_check_constraint(
        op.f("ck_report_videos_transcode_status"),
        "report_videos",
        f"transcode_status IN ({STATUSES})",
    )
    # Startup recovery must see every organization's jobs. SECURITY DEFINER bypasses row-level
    # security, so the function returns only what is needed to re-enqueue (ids); the job then
    # runs under its creator's access context as usual.
    op.execute(
        """
        CREATE FUNCTION app_unfinished_media_jobs()
        RETURNS TABLE (id uuid, created_by uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
        AS $$
            SELECT id, created_by FROM media_jobs
            WHERE status IN ('queued', 'running') AND created_by IS NOT NULL
            ORDER BY created_at
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION app_unfinished_media_jobs() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_unfinished_media_jobs() TO {_app_role()}")


def downgrade() -> None:
    op.execute("DROP FUNCTION app_unfinished_media_jobs()")
    op.execute(
        "UPDATE report_videos SET transcode_status = 'failed' WHERE transcode_status = 'uploading'"
    )
    op.drop_constraint(op.f("ck_report_videos_transcode_status"), "report_videos", type_="check")
    op.create_check_constraint(
        op.f("ck_report_videos_transcode_status"),
        "report_videos",
        f"transcode_status IN ({OLD_STATUSES})",
    )
