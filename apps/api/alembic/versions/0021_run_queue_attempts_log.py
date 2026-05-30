# 0021_run_queue_attempts_log.py
"""run_queue.attempts_log column for retry/replay history

Revision ID: 0021_run_queue_attempts_log
Revises: 0020_run_queue
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_run_queue_attempts_log"
down_revision: str | None = "0020_run_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_queue",
        sa.Column(
            "attempts_log",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("run_queue", "attempts_log")
