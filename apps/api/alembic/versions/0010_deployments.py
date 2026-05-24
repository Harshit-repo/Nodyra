"""deployments: parametrized schedulable workflow instances

Revision ID: 0010_deployments
Revises: 0009_node_run_debug
Create Date: 2026-05-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_deployments"
down_revision: str | None = "0009_node_run_debug"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(32),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("schedule_cron", sa.String(120), nullable=False, server_default=""),
        sa.Column(
            "schedule_interval", sa.String(20), nullable=False, server_default="hours"
        ),
        sa.Column("schedule_every", sa.Integer, nullable=False, server_default="1"),
        sa.Column("schedule_tz", sa.String(64), nullable=False, server_default=""),
        sa.Column("default_parameters", sa.JSON, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "environment_id",
            sa.String(32),
            sa.ForeignKey("environments.id"),
            nullable=True,
        ),
        sa.Column("last_fired", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index("ix_deployments_workflow_id", "deployments", ["workflow_id"])


def downgrade() -> None:
    op.drop_index("ix_deployments_workflow_id", table_name="deployments")
    op.drop_table("deployments")
