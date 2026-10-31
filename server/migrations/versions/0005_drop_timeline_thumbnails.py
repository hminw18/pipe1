"""drop the timeline thumbnail strip

The player no longer shows thumbnails on the timeline, so processing no longer builds them.
Strips already stored are left in the bucket under each video's folder.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("report_videos", "thumbnail_interval_seconds")
    op.drop_column("report_videos", "thumbnail_object_key")


def downgrade() -> None:
    op.add_column("report_videos", sa.Column("thumbnail_object_key", sa.Text(), nullable=True))
    op.add_column(
        "report_videos", sa.Column("thumbnail_interval_seconds", sa.Float(), nullable=True)
    )
