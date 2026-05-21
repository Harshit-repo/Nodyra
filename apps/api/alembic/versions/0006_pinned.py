"""pinned_data

Revision ID: 0006_pinned
Revises: 0005_enterprise
Create Date: 2026-05-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_pinned"
down_revision: str | None = "0005_enterprise"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pinned_data",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(32),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workflow_id", "node_id", name="uq_pinned_workflow_node"
        ),
    )
    op.create_index("ix_pinned_workflow_id", "pinned_data", ["workflow_id"])


def downgrade() -> None:
    op.drop_index("ix_pinned_workflow_id", table_name="pinned_data")
    op.drop_table("pinned_data")
