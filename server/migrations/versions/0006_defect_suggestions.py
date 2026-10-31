"""AI defect suggestions

Tracks from the AI detector, stored per report for an inspector to confirm or reject, and
the defect_detection job kind. The new table gets the same row-level security policy and
app-role grants as the tables in 0001.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31 00:00:00.000000

"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "'process_video', 'stop_analysis', 'defect_detection', 'export_excel', 'export_pdf',"
    " 'export_business'"
)
OLD_KINDS = "'process_video', 'stop_analysis', 'export_excel', 'export_pdf', 'export_business'"


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("app_role", "pipe1_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"invalid app_role: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "defect_suggestions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("item_category", sa.String(length=16), nullable=False),
        sa.Column("defect_item", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("best_ms", sa.BigInteger(), nullable=False),
        sa.Column("best_box", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "boxes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("defect_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')",
            name=op.f("ck_defect_suggestions_status"),
        ),
        sa.CheckConstraint(
            "item_category IN ('맨홀', '관로', '암거')",
            name=op.f("ck_defect_suggestions_item_category"),
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_defect_suggestions_confidence_range")
        ),
        sa.CheckConstraint(
            "0 <= start_ms AND start_ms <= best_ms AND best_ms <= end_ms",
            name=op.f("ck_defect_suggestions_time_order"),
        ),
        sa.ForeignKeyConstraint(
            ["defect_id"],
            ["report_defects.id"],
            name=op.f("fk_defect_suggestions_defect_id_report_defects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "project_id"],
            ["reports.id", "reports.project_id"],
            name=op.f("fk_defect_suggestions_report_id_project_id_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "report_id"],
            ["report_videos.id", "report_videos.report_id"],
            name=op.f("fk_defect_suggestions_video_id_report_id_report_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_defect_suggestions")),
    )
    op.create_index(
        "ix_defect_suggestions_report_time",
        "defect_suggestions",
        ["report_id", "start_ms"],
        unique=False,
    )
    op.drop_constraint(op.f("ck_media_jobs_kind"), "media_jobs", type_="check")
    op.create_check_constraint(op.f("ck_media_jobs_kind"), "media_jobs", f"kind IN ({KINDS})")

    op.execute("ALTER TABLE defect_suggestions ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY defect_suggestions_project_access ON defect_suggestions FOR ALL
        USING (app_can_access_project(project_id))
        WITH CHECK (app_can_access_project(project_id))
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON defect_suggestions TO {_app_role()}")


def downgrade() -> None:
    op.execute("DELETE FROM media_jobs WHERE kind = 'defect_detection'")
    op.drop_constraint(op.f("ck_media_jobs_kind"), "media_jobs", type_="check")
    op.create_check_constraint(op.f("ck_media_jobs_kind"), "media_jobs", f"kind IN ({OLD_KINDS})")
    op.drop_index("ix_defect_suggestions_report_time", table_name="defect_suggestions")
    op.drop_table("defect_suggestions")
