"""Video travel start

When the camera set off into the pipe, found by stop analysis; AI suggestions from before it
are hidden. A new column on an existing table: its row-level security and grants apply.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("report_videos", sa.Column("travel_start_s", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("report_videos", "travel_start_s")
