"""Denormalize run-level failure reason onto runs.

Revision ID: 0087_runs_error
Revises: 0086_run_queue_required_labels
"""

import sqlalchemy as sa
from alembic import op

revision = "0087_runs_error"
down_revision = "0086_run_queue_required_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "error")
