"""harden RLS for folders and directly scoped artifact/runner rows

Revision ID: 0058_tenant_rls_hardening
Revises: 0057_folder_color
"""
from __future__ import annotations

from alembic import op

revision: str = "0058_tenant_rls_hardening"
down_revision: str | None = "0057_folder_color"
branch_labels = None
depends_on = None

_GUC = "NULLIF(current_setting('app.current_org', true), '')"


def _direct_predicate() -> str:
    return f"({_GUC} IS NULL OR org_id = {_GUC})"


def _install_direct_policy(table: str) -> None:
    predicate = _direct_predicate()
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
    op.execute(
        f"CREATE POLICY org_isolation ON {table} "
        f"USING {predicate} WITH CHECK {predicate}"
    )


def upgrade() -> None:
    # 0054 initially populated the new columns with their server default. Fix
    # already-upgraded databases before switching the policies to direct org
    # checks; otherwise non-default historical rows would be misclassified.
    # Correlated subqueries work on both SQLite and Postgres, keeping the ORM
    # isolation layer correct for trusted-team SQLite installs too.
    op.execute(
        "UPDATE artifacts SET org_id = "
        "(SELECT r.org_id FROM runs AS r WHERE r.id = artifacts.run_id) "
        "WHERE run_id IS NOT NULL AND EXISTS "
        "(SELECT 1 FROM runs AS r WHERE r.id = artifacts.run_id)"
    )
    op.execute(
        "UPDATE runners SET org_id = "
        "(SELECT p.org_id FROM runner_pools AS p WHERE p.id = runners.pool_id) "
        "WHERE EXISTS (SELECT 1 FROM runner_pools AS p WHERE p.id = runners.pool_id)"
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    # folders was introduced after the original RLS migration. Artifacts and
    # runners now carry their own org_id, so their policies must use it rather
    # than the legacy parent lookup (whose nullable-artifact escape made every
    # browser upload visible to all tenants at the DB layer).
    for table in ("folders", "artifacts", "runners"):
        _install_direct_policy(table)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("DROP POLICY IF EXISTS org_isolation ON folders")
    op.execute("ALTER TABLE folders NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE folders DISABLE ROW LEVEL SECURITY")

    artifact_parent = (
        f"({_GUC} IS NULL OR run_id IS NULL OR "
        "EXISTS (SELECT 1 FROM runs __p WHERE __p.id = run_id))"
    )
    runner_parent = (
        f"({_GUC} IS NULL OR "
        "EXISTS (SELECT 1 FROM runner_pools __p WHERE __p.id = pool_id))"
    )
    for table, predicate in (
        ("artifacts", artifact_parent),
        ("runners", runner_parent),
    ):
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            f"USING {predicate} WITH CHECK {predicate}"
        )
