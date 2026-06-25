"""ALM-2: add missing FK constraints + index, rename pinned index

`alembic check` against Postgres (the production DB) revealed real model<->schema
drift: the ORM models declare 9 ForeignKey constraints (with ondelete cascades)
and the ix_runs_batch_id index that earlier migrations never created. So prod
Postgres had no DB-level referential integrity for those relations and the
declared ondelete (CASCADE / SET NULL) was not enforced.

This migration:
  * renames the pinned-data index to the SQLAlchemy-default name the model
    expects (ix_pinned_workflow_id -> ix_pinned_data_workflow_id);
  * creates the missing ix_runs_batch_id index (runs.batch_id, used by the
    batch-cancel query);
  * adds the 9 missing FK constraints to match the models exactly. All are
    SET NULL or CASCADE (no NO ACTION), so a parent delete cascades/nulls
    automatically and never blocks. workflows.environment_id is SET NULL
    (ALM-2: the runner treats a NULL env_id as the default env).

The index changes run on every dialect. The FK additions run on non-SQLite only:
SQLite can't ADD CONSTRAINT without recreating the (heavily-referenced, self-
referential) tables, doesn't enforce FK ondelete by default, and the test schema
is built via create_all (which already has the FKs). The authoritative drift
check therefore runs on Postgres (see .github/workflows/ci.yml).

Existing orphaned references are NULLed before each constraint is added so the
migration is safe on a populated database (all target columns are nullable).

Revision ID: 0039_fk_and_index_alignment
Revises: 0038_timestamp_not_null
Create Date: 2026-06-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_fk_and_index_alignment"
down_revision: str | None = "0038_timestamp_not_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (constraint_name, source_table, local_col, referent_table, ondelete)
_FKS = [
    ("fk_credentials_workflow_id", "credentials", "workflow_id", "workflows", "CASCADE"),
    ("fk_credentials_environment_id", "credentials", "environment_id", "environments", "CASCADE"),
    (
        "fk_deployments_workflow_version_id",
        "deployments",
        "workflow_version_id",
        "workflow_versions",
        "SET NULL",
    ),
    (
        "fk_deployments_error_workflow_id",
        "deployments",
        "error_workflow_id",
        "workflows",
        "SET NULL",
    ),
    ("fk_runs_workflow_version_id", "runs", "workflow_version_id", "workflow_versions", "SET NULL"),
    ("fk_runs_deployment_id", "runs", "deployment_id", "deployments", "SET NULL"),
    ("fk_runs_triggered_by_error_run_id", "runs", "triggered_by_error_run_id", "runs", "SET NULL"),
    ("fk_workflows_environment_id", "workflows", "environment_id", "environments", "SET NULL"),
    ("fk_workflows_error_workflow_id", "workflows", "error_workflow_id", "workflows", "SET NULL"),
]


def upgrade() -> None:
    # Index changes — safe on every dialect (plain index ops, no table rebuild).
    op.drop_index("ix_pinned_workflow_id", table_name="pinned_data")
    op.create_index("ix_pinned_data_workflow_id", "pinned_data", ["workflow_id"])
    op.create_index("ix_runs_batch_id", "runs", ["batch_id"])

    if op.get_bind().dialect.name == "sqlite":
        return  # see module docstring — FKs are Postgres-only here

    for name, table, col, ref, ondelete in _FKS:
        # Null dangling references so the constraint can be added on a populated DB.
        op.execute(
            f"UPDATE {table} SET {col} = NULL "
            f"WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT id FROM {ref})"
        )
        op.create_foreign_key(name, table, ref, [col], ["id"], ondelete=ondelete)


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        for name, table, *_ in _FKS:
            op.drop_constraint(name, table, type_="foreignkey")

    op.drop_index("ix_runs_batch_id", table_name="runs")
    op.drop_index("ix_pinned_data_workflow_id", table_name="pinned_data")
    op.create_index("ix_pinned_workflow_id", "pinned_data", ["workflow_id"])
