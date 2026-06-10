"""Phase A3 (multi-tenancy): org_id on every tenant-owned table.

Adds ``org_id`` (NOT NULL, server_default 'default', indexed) to the 14
top-level tenant-owned tables. ``server_default`` makes the backfill free on
both dialects: SQLite fills existing rows on ADD COLUMN, and Postgres 11+
records the default in catalog metadata without a table rewrite — no batched
UPDATE sweep is needed even on large ``runs`` tables.

Child tables (node_runs, run_events, run_approvals, artifacts, runners)
deliberately get no org_id — they inherit tenancy via their parent FK.

FK constraints follow the 0039 precedent: added on non-SQLite only (SQLite
can't ADD CONSTRAINT without rebuilding heavily-referenced tables, doesn't
enforce ondelete by default, and the test schema comes from create_all which
already carries the FKs). Postgres is the authoritative integrity check.

Revision ID: 0041_org_id_cols
Revises: 0040_orgs
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0041_org_id_cols"
down_revision: str | None = "0040_orgs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_SCOPED_TABLES = [
    "workflows",
    "workflow_versions",
    "credentials",
    "environments",
    "deployments",
    "code_modules",
    "pinned_data",
    "runner_pools",
    "runs",
    "run_batches",
    "run_queue",
    "provider_trigger_subscriptions",
    "schedule_state",
    "audit_events",
]


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    for table in ORG_SCOPED_TABLES:
        if "org_id" in _columns(table):
            continue
        op.add_column(
            table,
            sa.Column(
                "org_id",
                sa.String(32),
                nullable=False,
                server_default="default",
            ),
        )
        op.create_index(f"ix_{table}_org_id", table, ["org_id"])
        if not is_sqlite:
            op.create_foreign_key(
                f"fk_{table}_org_id_organizations",
                table,
                "organizations",
                ["org_id"],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    for table in reversed(ORG_SCOPED_TABLES):
        if "org_id" not in _columns(table):
            continue
        if not is_sqlite:
            op.drop_constraint(
                f"fk_{table}_org_id_organizations", table, type_="foreignkey"
            )
        op.drop_index(f"ix_{table}_org_id", table_name=table)
        op.drop_column(table, "org_id")
