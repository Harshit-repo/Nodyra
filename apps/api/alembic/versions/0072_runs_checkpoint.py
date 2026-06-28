"""Add checkpoint column to runs for durable execution.

Revision ID: 0072
Revises: 0071
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("checkpoint", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "checkpoint")
