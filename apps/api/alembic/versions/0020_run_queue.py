# 0020_run_queue.py
"""durable run queue table

Revision ID: 0020_run_queue
Revises: 0019_runner_ssh
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_run_queue"
down_revision: str | None = "0019_runner_ssh"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_queue",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=32),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("environment_id", sa.String(length=32), nullable=True),
        sa.Column("runner_pool_id", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "queue_reason", sa.String(length=40), nullable=False, server_default=""
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("leased_by", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_run_queue_workflow_id", "run_queue", ["workflow_id"])
    op.create_index(
        "ix_run_queue_status_available_at",
        "run_queue",
        ["status", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_queue_status_available_at", table_name="run_queue")
    op.drop_index("ix_run_queue_workflow_id", table_name="run_queue")
    op.drop_table("run_queue")
