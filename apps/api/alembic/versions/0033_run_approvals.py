# 0033_run_approvals.py
"""run approval records

Stores operator decisions for AI agent tool calls that require approval before
side-effecting tools are allowed to run.

Revision ID: 0033_run_approvals
Revises: 0032_run_events
Create Date: 2026-06-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_run_approvals"
down_revision: str | None = "0032_run_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_approvals",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("approval_key", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("node_id", sa.String(length=120), nullable=True),
        sa.Column("agent_node_id", sa.String(length=120), nullable=True),
        sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_steps", sa.Integer(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=160), nullable=False),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("resume_state", sa.JSON(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "approval_key", name="uq_run_approvals_key"),
    )
    op.create_index("ix_run_approvals_run_id", "run_approvals", ["run_id"])
    op.create_index(
        "ix_run_approvals_run_id_status",
        "run_approvals",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_approvals_run_id_status", table_name="run_approvals")
    op.drop_index("ix_run_approvals_run_id", table_name="run_approvals")
    op.drop_table("run_approvals")
