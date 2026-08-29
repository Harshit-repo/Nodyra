"""Close the RLS coverage gap on four org-scoped tables (F-03)

``environment_build_jobs``, ``memberships``, ``workflow_checks`` and
``workflow_revisions`` all carry an ``org_id`` but were created without a
row-level-security policy, so only the ORM layer scoped them. That layer filters
SELECTs; it does not constrain raw SQL or bulk UPDATE/DELETE. RLS is the second,
independent layer that exists precisely to catch an ORM mistake — these four had
only one layer.

``memberships`` is deliberately included even though ``/orgs`` legitimately reads
it across orgs to answer "which organizations am I in": that path already opts
out at the ORM layer via ``skip_org_filter``, and this policy's GUC-is-NULL
escape keeps it working for the system contexts that need it.

Revision ID: 0093_rls_coverage_gap
Revises: 0092_schema_drift_alignment
"""
from __future__ import annotations

from alembic import op

revision: str = "0093_rls_coverage_gap"
down_revision: str | None = "0092_schema_drift_alignment"
branch_labels = None
depends_on = None

_GUC = "NULLIF(current_setting('app.current_org', true), '')"
_TABLES = (
    "environment_build_jobs",
    "memberships",
    "workflow_checks",
    "workflow_revisions",
)


def _predicate() -> str:
    return f"({_GUC} IS NULL OR org_id = {_GUC})"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
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
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
