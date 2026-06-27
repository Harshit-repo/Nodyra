"""Add partial unique index on runs.deduplication_key (D-01)

Two concurrent webhook deliveries could both pass a "no active run for this key"
check before either commits, creating duplicate runs. A unique index prevents it
at the DB level. Postgres supports WHERE-predicate partial indexes (NULLs
excluded); SQLite treats all NULLs as distinct, so a regular unique index works.

Revision ID: 0061_runs_dedup_unique
Revises: 0060_mcp_production_hardening
"""
from __future__ import annotations

from alembic import op

revision: str = "0061_runs_dedup_unique"
down_revision: str | None = "0060_mcp_production_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Remove the existing plain non-unique index first.
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_index("ix_runs_deduplication_key", if_exists=True)

    if bind.dialect.name == "postgresql":
        # Partial unique index: NULL values are excluded so multiple runs with
        # deduplication_key=NULL are still allowed (they simply have no key).
        op.execute(
            "CREATE UNIQUE INDEX uq_runs_deduplication_key "
            "ON runs (deduplication_key) "
            "WHERE deduplication_key IS NOT NULL"
        )
    else:
        # SQLite: NULL != NULL in UNIQUE constraints, so this behaves correctly.
        op.create_index(
            "uq_runs_deduplication_key",
            "runs",
            ["deduplication_key"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS uq_runs_deduplication_key")
    else:
        op.drop_index("uq_runs_deduplication_key", table_name="runs")
    op.create_index("ix_runs_deduplication_key", "runs", ["deduplication_key"])
