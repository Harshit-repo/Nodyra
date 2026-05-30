# 0023_workflow_allow_concurrent.py
"""workflows.allow_concurrent column

Revision ID: 0023_workflow_allow_concurrent
Revises: 0022_run_queue_replay_seed
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_workflow_allow_concurrent"
down_revision: str | None = "0022_run_queue_replay_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "allow_concurrent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workflows", "allow_concurrent")
