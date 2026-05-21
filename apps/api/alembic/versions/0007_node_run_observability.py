"""node_run observability: logs + timing

Revision ID: 0007_node_run_observability
Revises: 0006_pinned
Create Date: 2026-05-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_node_run_observability"
down_revision: str | None = "0006_pinned"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("node_runs", sa.Column("logs", sa.JSON(), nullable=True))
    op.add_column("node_runs", sa.Column("started_at", sa.Float(), nullable=True))
    op.add_column("node_runs", sa.Column("finished_at", sa.Float(), nullable=True))
    op.add_column("node_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("node_runs", "duration_ms")
    op.drop_column("node_runs", "finished_at")
    op.drop_column("node_runs", "started_at")
    op.drop_column("node_runs", "logs")
