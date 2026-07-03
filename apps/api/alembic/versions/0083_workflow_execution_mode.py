"""Workflow-level execution mode + per-run sandbox escalation.

Revision ID: 0083_workflow_execution_mode
Revises: 0082_mcp_connection_policy
"""

import sqlalchemy as sa
from alembic import op

revision = "0083_workflow_execution_mode"
down_revision = "0082_mcp_connection_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "execution_mode",
            sa.String(length=20),
            nullable=False,
            server_default="inherit",
        ),
    )
    op.add_column(
        "runs",
        sa.Column("execution_mode", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "execution_mode")
    op.drop_column("workflows", "execution_mode")
