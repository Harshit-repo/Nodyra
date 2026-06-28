"""Add tests JSON column to workflows table.

Revision ID: 0073
Revises: 0072
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("tests", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflows", "tests")
