"""provider trigger subscriptions

Revision ID: 0034_provider_triggers
Revises: 0033_run_approvals
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0034_provider_triggers"
down_revision: str | None = "0033_run_approvals"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "provider_trigger_subscriptions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=32), nullable=True),
        sa.Column("node_id", sa.String(length=120), nullable=False),
        sa.Column("node_type", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("trigger_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=240), nullable=False),
        sa.Column("callback_url", sa.Text(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"],
            ["workflow_versions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id",
            "node_id",
            name="uq_provider_trigger_subscriptions_workflow_node",
        ),
    )
    op.create_index(
        "ix_provider_trigger_subscriptions_workflow_id",
        "provider_trigger_subscriptions",
        ["workflow_id"],
    )
    op.create_index(
        "ix_provider_trigger_subscriptions_workflow_version_id",
        "provider_trigger_subscriptions",
        ["workflow_version_id"],
    )
    op.create_index(
        "ix_provider_trigger_subscriptions_provider_status",
        "provider_trigger_subscriptions",
        ["provider", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_trigger_subscriptions_provider_status",
        table_name="provider_trigger_subscriptions",
    )
    op.drop_index(
        "ix_provider_trigger_subscriptions_workflow_version_id",
        table_name="provider_trigger_subscriptions",
    )
    op.drop_index(
        "ix_provider_trigger_subscriptions_workflow_id",
        table_name="provider_trigger_subscriptions",
    )
    op.drop_table("provider_trigger_subscriptions")
