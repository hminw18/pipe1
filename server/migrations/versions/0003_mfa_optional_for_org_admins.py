"""MFA optional for org admins

Only platform admins must use MFA now. Org admins invited earlier were flagged
mfa_required; clear it so they may skip or disable MFA like inspectors. Enrolled
TOTP stays in place.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE users SET mfa_required = false WHERE role <> 'platform_admin'")


def downgrade() -> None:
    op.execute("UPDATE users SET mfa_required = true WHERE role = 'org_admin'")
