"""schedule_state: durable last_fired per workflow

Revision ID: 0008_schedule_state
Revises: 0007_node_run_observability
Create Date: 2026-05-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_schedule_state"
down_revision: str | None = "0007_node_run_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedule_state",
        sa.Column(
            "workflow_id",
            sa.String(32),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_fired", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("schedule_state")
