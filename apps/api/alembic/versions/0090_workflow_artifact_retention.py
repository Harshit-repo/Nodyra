"""Add per-workflow artifact retention.

Revision ID: 0090_workflow_artifact_retention
Revises: 0089_workflow_checks
"""

import sqlalchemy as sa
from alembic import op

revision = "0090_workflow_artifact_retention"
down_revision = "0089_workflow_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("artifact_retention_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflows", "artifact_retention_days")
