"""Phase A4 (multi-tenancy): Postgres row-level security policies.

The DB-enforced backstop (Layer 1): even if application code forgets an org
filter, Postgres refuses to return another org's rows. SQLite has no RLS —
this migration is a no-op there, which is exactly why the Postgres test lane
(A5/A6 in the plan) is the only proof these policies work.

Policy semantics — fail-open when the GUC is unset, fail-closed when set:

* ``app.current_org`` **set** (every ORM transaction while
  ``multi_tenancy_enabled`` is on — see ``app/tenancy.py`` ``after_begin``):
  only rows of that org are visible/writable. This is the backstop against
  missed org scoping in application queries.
* ``app.current_org`` **unset** (flag off, migrations, ops scripts, psql):
  unrestricted, preserving single-tenant behaviour and operability. The
  hardening upgrade — a dedicated app DB role with no fail-open escape and a
  separate maintenance role — is deliberately deferred to the funded Phase D
  build (it doubles the ops surface for self-hosted installs).

``FORCE`` is required: self-hosted Nodyra connects as the table owner, and
without FORCE the owner bypasses policies entirely.

Child tables (node_runs, run_events, run_approvals, artifacts via ``runs``;
runners via ``runner_pools``) carry no org_id; their policies delegate via
EXISTS to the parent, whose own RLS applies inside the policy subquery.
``organizations``/``memberships`` are identity tables, not tenant data —
app-level authorization governs them (Phase B); no RLS here.

Revision ID: 0042_rls
Revises: 0041_org_id_cols
Create Date: 2026-06-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0042_rls"
down_revision: str | None = "0041_org_id_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_TABLES = [
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

# child table -> (fk column, parent table). Artifacts' run_id is nullable
# (browser uploads); NULL-parent rows stay visible (org-agnostic today,
# namespaced in Phase F).
CHILD_TABLES = {
    "node_runs": ("run_id", "runs", False),
    "run_events": ("run_id", "runs", False),
    "run_approvals": ("run_id", "runs", False),
    "artifacts": ("run_id", "runs", True),
    "runners": ("pool_id", "runner_pools", False),
}

_GUC = "NULLIF(current_setting('app.current_org', true), '')"


def _org_predicate() -> str:
    return f"({_GUC} IS NULL OR org_id = {_GUC})"


def _child_predicate(fk: str, parent: str, nullable_fk: bool) -> str:
    exists = (
        f"EXISTS (SELECT 1 FROM {parent} __p WHERE __p.id = {fk})"
    )
    null_escape = f"{fk} IS NULL OR " if nullable_fk else ""
    return f"({_GUC} IS NULL OR {null_escape}{exists})"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ORG_TABLES:
        predicate = _org_predicate()
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            f"USING {predicate} WITH CHECK {predicate}"
        )
    for table, (fk, parent, nullable_fk) in CHILD_TABLES.items():
        predicate = _child_predicate(fk, parent, nullable_fk)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            f"USING {predicate} WITH CHECK {predicate}"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in [*ORG_TABLES, *CHILD_TABLES]:
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
