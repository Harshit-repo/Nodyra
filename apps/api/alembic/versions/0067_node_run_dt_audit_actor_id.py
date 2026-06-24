"""D-12/D-13: NodeRun timestamp columns → DateTime; AuditEvent actor_id → String(32).

D-12: node_runs.started_at and finished_at were Float (epoch seconds), inconsistent
with all other timestamp columns that use TIMESTAMP WITH TIME ZONE.  We convert in
place using Postgres's to_timestamp() so existing wall-clock data is preserved exactly.

D-13: audit_events.actor_id was VARCHAR(36) (UUID with dashes) but all internal IDs
use uuid4().hex (32-char hex, no dashes).  All stored values are either NULL or 32
chars, so narrowing to VARCHAR(32) is safe.

Revision ID: 0067_node_run_dt_audit_actor_id
Revises: 0066_runner_fk_constraints
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_node_run_dt_audit_actor_id"
down_revision: str | None = "0066_runner_fk_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # D-12: convert Float epoch-seconds columns to TIMESTAMPTZ.
    # to_timestamp() in Postgres interprets the float as Unix epoch seconds and
    # returns a TIMESTAMPTZ value; NULL floats become NULL timestamps.
    bind.execute(sa.text(
        "ALTER TABLE node_runs "
        "ALTER COLUMN started_at TYPE TIMESTAMP WITH TIME ZONE "
        "USING to_timestamp(started_at)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE node_runs "
        "ALTER COLUMN finished_at TYPE TIMESTAMP WITH TIME ZONE "
        "USING to_timestamp(finished_at)"
    ))

    # D-13: narrow actor_id from VARCHAR(36) to VARCHAR(32).
    # All stored values are either NULL or 32-char hex UUIDs (uuid4().hex).
    # Postgres allows narrowing VARCHAR if no existing row exceeds the new width.
    bind.execute(sa.text(
        "ALTER TABLE audit_events "
        "ALTER COLUMN actor_id TYPE VARCHAR(32)"
    ))


def downgrade() -> None:
    bind = op.get_bind()

    # Restore actor_id width (no data loss — widening always succeeds).
    bind.execute(sa.text(
        "ALTER TABLE audit_events "
        "ALTER COLUMN actor_id TYPE VARCHAR(36)"
    ))

    # Restore Float columns. epoch() extracts seconds since Unix epoch as float.
    bind.execute(sa.text(
        "ALTER TABLE node_runs "
        "ALTER COLUMN started_at TYPE DOUBLE PRECISION "
        "USING EXTRACT(EPOCH FROM started_at)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE node_runs "
        "ALTER COLUMN finished_at TYPE DOUBLE PRECISION "
        "USING EXTRACT(EPOCH FROM finished_at)"
    ))
