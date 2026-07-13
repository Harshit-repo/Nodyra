"""Add persisted run trace id.

Revision ID: 0091_runs_trace_id
Revises: 0090_workflow_artifact_retention
"""

import sqlalchemy as sa
from alembic import op

revision = "0091_runs_trace_id"
down_revision = "0090_workflow_artifact_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("trace_id", sa.String(length=32), nullable=True))
    op.create_index("ix_runs_trace_id", "runs", ["trace_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_runs_trace_id", table_name="runs")
    op.drop_column("runs", "trace_id")
