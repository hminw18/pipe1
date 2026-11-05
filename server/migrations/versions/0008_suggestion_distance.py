"""Suggestion distance

The depth counter at a suggestion's best frame, from stop analysis (on the GPU server or in
Pipe1's worker). A new column on an existing table: its row-level security and grants apply.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("defect_suggestions", sa.Column("distance_m", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("defect_suggestions", "distance_m")
