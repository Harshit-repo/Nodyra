"""Add ix_runs_org_status_started composite index for multi-tenant run listings.

Under multi-tenancy the dominant run-listing query is::

    SELECT ... FROM runs
    WHERE org_id = $1 AND status = $2
    ORDER BY started_at DESC

The existing ``ix_runs_status_started_at`` index lacks the ``org_id`` prefix,
so Postgres falls back to a full scan filtered by org_id.  This index covers
the full (org_id, status, started_at) tuple.

Revision ID: 0069
Revises: 0068
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0069"
down_revision: str | None = "0068_runners_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_runs_org_status_started",
        "runs",
        ["org_id", "status", "started_at"],
        postgresql_where=text("status IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_runs_org_status_started", table_name="runs")
