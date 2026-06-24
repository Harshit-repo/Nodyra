"""Add FK constraints on runner_pool_id and runner_id columns (D-07)

Several columns that logically reference runner_pools.id / runners.id were
stored as plain String(32) with no database-level foreign key. This migration
adds the constraints so the database enforces referential integrity and makes
ON DELETE SET NULL semantics explicit.

Affected columns:
  runs.runner_pool_id      → runner_pools.id ON DELETE SET NULL
  runs.runner_id           → runners.id      ON DELETE SET NULL
  deployments.runner_pool_id → runner_pools.id ON DELETE SET NULL
  workflows.default_runner_pool_id → runner_pools.id ON DELETE SET NULL
  credentials.runner_pool_id → runner_pools.id ON DELETE SET NULL

Revision ID: 0066_runner_fk_constraints
Revises: 0065_audit_events_index
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0066_runner_fk_constraints"
down_revision = "0065_audit_events_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        # runs.runner_pool_id
        op.execute(
            "ALTER TABLE runs ADD CONSTRAINT fk_runs_runner_pool_id "
            "FOREIGN KEY (runner_pool_id) REFERENCES runner_pools(id) ON DELETE SET NULL"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_runs_runner_pool_id "
            "ON runs (runner_pool_id)"
        )
        # runs.runner_id
        op.execute(
            "ALTER TABLE runs ADD CONSTRAINT fk_runs_runner_id "
            "FOREIGN KEY (runner_id) REFERENCES runners(id) ON DELETE SET NULL"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_runs_runner_id "
            "ON runs (runner_id)"
        )
        # deployments.runner_pool_id
        op.execute(
            "ALTER TABLE deployments ADD CONSTRAINT fk_deployments_runner_pool_id "
            "FOREIGN KEY (runner_pool_id) REFERENCES runner_pools(id) ON DELETE SET NULL"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_deployments_runner_pool_id "
            "ON deployments (runner_pool_id)"
        )
        # workflows.default_runner_pool_id
        op.execute(
            "ALTER TABLE workflows ADD CONSTRAINT fk_workflows_default_runner_pool_id "
            "FOREIGN KEY (default_runner_pool_id) REFERENCES runner_pools(id) ON DELETE SET NULL"
        )
        # credentials.runner_pool_id
        op.execute(
            "ALTER TABLE credentials ADD CONSTRAINT fk_credentials_runner_pool_id "
            "FOREIGN KEY (runner_pool_id) REFERENCES runner_pools(id) ON DELETE SET NULL"
        )
    else:
        # SQLite: FK constraints cannot be added to existing tables; indices can.
        # The FK is enforced at the application layer via SQLAlchemy model definitions.
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_runs_runner_pool_id ON runs (runner_pool_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_runs_runner_id ON runs (runner_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_deployments_runner_pool_id "
            "ON deployments (runner_pool_id)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.execute("DROP INDEX IF EXISTS ix_runs_runner_pool_id")
        op.execute("DROP INDEX IF EXISTS ix_runs_runner_id")
        op.execute("DROP INDEX IF EXISTS ix_deployments_runner_pool_id")
        op.execute(
            "ALTER TABLE runs DROP CONSTRAINT IF EXISTS fk_runs_runner_pool_id"
        )
        op.execute(
            "ALTER TABLE runs DROP CONSTRAINT IF EXISTS fk_runs_runner_id"
        )
        op.execute(
            "ALTER TABLE deployments DROP CONSTRAINT IF EXISTS fk_deployments_runner_pool_id"
        )
        op.execute(
            "ALTER TABLE workflows DROP CONSTRAINT IF EXISTS fk_workflows_default_runner_pool_id"
        )
        op.execute(
            "ALTER TABLE credentials DROP CONSTRAINT IF EXISTS fk_credentials_runner_pool_id"
        )
    else:
        op.execute("DROP INDEX IF EXISTS ix_runs_runner_pool_id")
        op.execute("DROP INDEX IF EXISTS ix_runs_runner_id")
        op.execute("DROP INDEX IF EXISTS ix_deployments_runner_pool_id")
