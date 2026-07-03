"""Add per-workflow Python requirement lines.

Revision ID: 0085_workflow_requirements
Revises: 0084_workflow_sandbox_resources
"""

import sqlalchemy as sa
from alembic import op

revision = "0085_workflow_requirements"
down_revision = "0084_workflow_sandbox_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("requirements", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("workflows", "requirements")
