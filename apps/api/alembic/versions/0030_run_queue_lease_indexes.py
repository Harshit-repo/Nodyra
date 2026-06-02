# 0030_run_queue_lease_indexes.py
"""run_queue lease-path indexes

Adds composite indexes that harden the durable run-queue lease path for
multi-replica dispatch:

* ``ix_run_queue_lease`` on ``(status, priority, available_at)`` matches the
  lease query's filter and ``ORDER BY priority DESC, available_at ASC`` so the
  ``FOR UPDATE SKIP LOCKED`` candidate is found via the index instead of a
  scan+sort (which would lock extra rows and raise contention).
* ``ix_run_queue_status_lease_expires`` on ``(status, lease_expires_at)`` backs
  the expired-lease sweep in ``requeue_expired_leases``.

Revision ID: 0030_run_queue_lease_indexes
Revises: 0029_environment_runner_pool
Create Date: 2026-06-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_run_queue_lease_indexes"
down_revision: str | None = "0029_environment_runner_pool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _indexes(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    existing = _indexes("run_queue")
    if "ix_run_queue_lease" not in existing:
        op.create_index(
            "ix_run_queue_lease",
            "run_queue",
            ["status", "priority", "available_at"],
        )
    if "ix_run_queue_status_lease_expires" not in existing:
        op.create_index(
            "ix_run_queue_status_lease_expires",
            "run_queue",
            ["status", "lease_expires_at"],
        )


def downgrade() -> None:
    existing = _indexes("run_queue")
    if "ix_run_queue_status_lease_expires" in existing:
        op.drop_index("ix_run_queue_status_lease_expires", table_name="run_queue")
    if "ix_run_queue_lease" in existing:
        op.drop_index("ix_run_queue_lease", table_name="run_queue")
