# 0025_run_dedup_key.py
"""runs deduplication_key column

Revision ID: 0025_run_dedup_key
Revises: 0024_audit_actor
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_run_dedup_key"
down_revision: str | None = "0024_audit_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("deduplication_key", sa.String(128), nullable=True),
    )
    op.create_index("ix_runs_deduplication_key", "runs", ["deduplication_key"])


def downgrade() -> None:
    op.drop_index("ix_runs_deduplication_key", table_name="runs")
    op.drop_column("runs", "deduplication_key")
