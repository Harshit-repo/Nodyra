"""Add dispatch-worker labels to run queue entries.

Revision ID: 0086_run_queue_required_labels
Revises: 0085_workflow_requirements
"""

import sqlalchemy as sa
from alembic import op

revision = "0086_run_queue_required_labels"
down_revision = "0085_workflow_requirements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_queue", sa.Column("required_labels", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_queue", "required_labels")
