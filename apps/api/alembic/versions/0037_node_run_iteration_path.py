"""node_runs: add iteration_path for per-iteration loop node runs

Revision ID: 0037_node_run_iteration
Revises: 0036_artifact_nullable_run
Create Date: 2026-06-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_node_run_iteration"
down_revision: str | None = "0036_artifact_nullable_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("node_runs") as batch:
        batch.add_column(sa.Column("iteration_path", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("node_runs") as batch:
        batch.drop_column("iteration_path")
