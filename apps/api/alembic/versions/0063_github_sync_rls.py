"""Add RLS policies to github_sync_jobs and github_sync_configs + hot-path index (D-03/D-05)

github_sync_jobs and github_sync_configs were created in 0059 without RLS policies,
so they are unprotected at the DB layer. Add the same direct org_isolation policy
used by other tenant tables (matching the pattern in 0058_tenant_rls_hardening).
Also add a composite index on (org_id, status, next_retry_at) for the sync worker's
hot query path.

Revision ID: 0063_github_sync_rls
Revises: 0062_workflow_version_unique
"""
from __future__ import annotations

from alembic import op

revision: str = "0063_github_sync_rls"
down_revision: str | None = "0062_workflow_version_unique"
branch_labels = None
depends_on = None

_GUC = "NULLIF(current_setting('app.current_org', true), '')"
_TABLES = ("github_sync_configs", "github_sync_jobs")


def _predicate() -> str:
    return f"({_GUC} IS NULL OR org_id = {_GUC})"


def upgrade() -> None:
    # Hot-path composite index — applies to both Postgres and SQLite.
    op.create_index(
        "ix_github_sync_jobs_org_status_retry",
        "github_sync_jobs",
        ["org_id", "status", "next_retry_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    predicate = _predicate()
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            f"USING {predicate} WITH CHECK {predicate}"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_github_sync_jobs_org_status_retry", table_name="github_sync_jobs")
