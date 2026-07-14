"""Align the PostgreSQL schema with the ORM contract.

Revision ID: 0092_schema_drift_alignment
Revises: 0091_runs_trace_id
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0092_schema_drift_alignment"
down_revision: str | None = "0091_runs_trace_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORG_GUC = "NULLIF(current_setting('app.current_org', true), '')"
_ORG_PREDICATE = f"({_ORG_GUC} IS NULL OR org_id = {_ORG_GUC})"


def _drop_org_policies_for_type_change() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP POLICY IF EXISTS mcp_connections_org ON mcp_connections")
    op.execute("DROP POLICY IF EXISTS sso_configs_org ON sso_configs")


def _restore_org_policies_after_type_change() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "CREATE POLICY mcp_connections_org ON mcp_connections "
        f"USING {_ORG_PREDICATE} WITH CHECK {_ORG_PREDICATE}"
    )
    op.execute(
        "CREATE POLICY sso_configs_org ON sso_configs "
        f"USING {_ORG_PREDICATE} WITH CHECK {_ORG_PREDICATE}"
    )


def _backfill_required_timestamps(table_name: str, *columns: str) -> None:
    for column in columns:
        op.execute(
            sa.text(
                f'UPDATE "{table_name}" SET "{column}" = CURRENT_TIMESTAMP '
                f'WHERE "{column}" IS NULL'
            )
        )


def upgrade() -> None:
    # Normalise legacy identifier widths. Existing IDs are 32-character UUID
    # hex strings; early migrations used Text/VARCHAR(120) accidentally.
    with op.batch_alter_table("credentials") as batch:
        batch.alter_column(
            "runner_pool_id",
            existing_type=sa.String(120),
            type_=sa.String(32),
            existing_nullable=True,
        )
    # PostgreSQL policies bind to the original column type expression and must
    # be recreated around an ALTER TYPE, even for binary-compatible text types.
    _drop_org_policies_for_type_change()
    with op.batch_alter_table("mcp_connections") as batch:
        batch.alter_column(
            "org_id",
            existing_type=sa.Text(),
            type_=sa.String(32),
            existing_nullable=False,
        )
    with op.batch_alter_table("sso_configs") as batch:
        batch.alter_column(
            "org_id",
            existing_type=sa.Text(),
            type_=sa.String(32),
            existing_nullable=False,
        )
    _restore_org_policies_after_type_change()

    # Backfill before tightening nullability so upgrades remain safe for rows
    # written by versions whose ORM defaults were not server constraints.
    _backfill_required_timestamps("environment_build_jobs", "created_at", "updated_at")
    with op.batch_alter_table("environment_build_jobs") as batch:
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    _backfill_required_timestamps("workflow_checks", "created_at", "updated_at")
    with op.batch_alter_table("workflow_checks") as batch:
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    _backfill_required_timestamps("workflow_revisions", "created_at")
    with op.batch_alter_table("workflow_revisions") as batch:
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    # Restore ORM-declared foreign keys and their hot-path lookup indexes.
    with op.batch_alter_table("run_batches") as batch:
        batch.create_foreign_key(
            "fk_run_batches_deployment_id",
            "deployments",
            ["deployment_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("runs") as batch:
        batch.create_foreign_key(
            "fk_runs_batch_id",
            "run_batches",
            ["batch_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index("ix_deployments_environment_id", "deployments", ["environment_id"])
    op.create_index(
        "ix_github_sync_configs_credential_id",
        "github_sync_configs",
        ["credential_id"],
    )
    op.create_index("ix_run_queue_environment_id", "run_queue", ["environment_id"])
    op.create_index("ix_run_queue_runner_pool_id", "run_queue", ["runner_pool_id"])
    op.create_index(
        "ix_workflows_default_runner_pool_id",
        "workflows",
        ["default_runner_pool_id"],
    )
    op.create_index("ix_workflows_environment_id", "workflows", ["environment_id"])


def downgrade() -> None:
    op.drop_index("ix_workflows_environment_id", table_name="workflows")
    op.drop_index("ix_workflows_default_runner_pool_id", table_name="workflows")
    op.drop_index("ix_run_queue_runner_pool_id", table_name="run_queue")
    op.drop_index("ix_run_queue_environment_id", table_name="run_queue")
    op.drop_index(
        "ix_github_sync_configs_credential_id",
        table_name="github_sync_configs",
    )
    op.drop_index("ix_deployments_environment_id", table_name="deployments")

    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("fk_runs_batch_id", type_="foreignkey")
    with op.batch_alter_table("run_batches") as batch:
        batch.drop_constraint("fk_run_batches_deployment_id", type_="foreignkey")

    with op.batch_alter_table("workflow_revisions") as batch:
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
    with op.batch_alter_table("workflow_checks") as batch:
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
    with op.batch_alter_table("environment_build_jobs") as batch:
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )

    _drop_org_policies_for_type_change()
    with op.batch_alter_table("sso_configs") as batch:
        batch.alter_column(
            "org_id",
            existing_type=sa.String(32),
            type_=sa.Text(),
            existing_nullable=False,
        )
    with op.batch_alter_table("mcp_connections") as batch:
        batch.alter_column(
            "org_id",
            existing_type=sa.String(32),
            type_=sa.Text(),
            existing_nullable=False,
        )
    _restore_org_policies_after_type_change()
    with op.batch_alter_table("credentials") as batch:
        batch.alter_column(
            "runner_pool_id",
            existing_type=sa.String(32),
            type_=sa.String(120),
            existing_nullable=True,
        )
