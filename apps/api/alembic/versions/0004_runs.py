"""runs and node_runs

Revision ID: 0004_runs
Revises: 0003_environments
Create Date: 2026-05-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_runs"
down_revision: str | None = "0003_environments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(32),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column(
            "trigger_type", sa.String(20), nullable=False, server_default="manual"
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_runs_workflow_id", "runs", ["workflow_id"])

    op.create_table(
        "node_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_node_runs_run_id", "node_runs", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_node_runs_run_id", table_name="node_runs")
    op.drop_table("node_runs")
    op.drop_index("ix_runs_workflow_id", table_name="runs")
    op.drop_table("runs")
