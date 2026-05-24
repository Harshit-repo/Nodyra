"""node_run debug metadata

Revision ID: 0009_node_run_debug
Revises: 0008_schedule_state
Create Date: 2026-05-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_node_run_debug"
down_revision: str | None = "0008_schedule_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("node_runs", sa.Column("debug", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("node_runs", "debug")
