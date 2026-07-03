"""Per-workflow sandbox resource requests.

Revision ID: 0084_workflow_sandbox_resources
Revises: 0083_workflow_execution_mode
"""

import sqlalchemy as sa
from alembic import op

revision = "0084_workflow_sandbox_resources"
down_revision = "0083_workflow_execution_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("sandbox_resources", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflows", "sandbox_resources")
