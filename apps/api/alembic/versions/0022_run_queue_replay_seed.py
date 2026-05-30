# 0022_run_queue_replay_seed.py
"""run_queue.replay_seed column for replay-from-failure seeding

Revision ID: 0022_run_queue_replay_seed
Revises: 0021_run_queue_attempts_log
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_run_queue_replay_seed"
down_revision: str | None = "0021_run_queue_attempts_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_queue",
        sa.Column("replay_seed", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_queue", "replay_seed")
