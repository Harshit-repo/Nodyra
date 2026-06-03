# 0032_run_events.py
"""durable run events

Stores non-node execution events, starting with AI agent step/tool events, so
live streamed details also appear in run timeline history.

Revision ID: 0032_run_events
Revises: 0031_run_webhook_response
Create Date: 2026-06-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_run_events"
down_revision: str | None = "0031_run_webhook_response"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("node_id", sa.String(length=120), nullable=True),
        sa.Column("agent_node_id", sa.String(length=120), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_event_type", "run_events", ["event_type"])
    op.create_index("ix_run_events_run_id_sequence", "run_events", ["run_id", "sequence"])
    op.create_index("ix_run_events_run_id_ts", "run_events", ["run_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_run_events_run_id_ts", table_name="run_events")
    op.drop_index("ix_run_events_run_id_sequence", table_name="run_events")
    op.drop_index("ix_run_events_event_type", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
