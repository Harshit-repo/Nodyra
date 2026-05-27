"""production slices: scoped credentials, releases, error workflows, ai drafts

Revision ID: 0013_production_slices
Revises: 0012_artifacts
Create Date: 2026-05-25

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0013_production_slices"
down_revision: str | None = "0012_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def _create_index_once(name: str, table: str, columns: list[str]) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns)


def upgrade() -> None:
    existing = _columns("credentials")
    with op.batch_alter_table("credentials") as batch:
        if "scope" not in existing:
            batch.add_column(
                sa.Column(
                    "scope",
                    sa.String(20),
                    nullable=False,
                    server_default="global",
                )
            )
        if "workflow_id" not in existing:
            batch.add_column(
                sa.Column(
                    "workflow_id",
                    sa.String(32),
                    nullable=True,
                )
            )
        if "environment_id" not in existing:
            batch.add_column(
                sa.Column(
                    "environment_id",
                    sa.String(32),
                    nullable=True,
                )
            )
        if "runner_pool_id" not in existing:
            batch.add_column(sa.Column("runner_pool_id", sa.String(120), nullable=True))
        if "description" not in existing:
            batch.add_column(
                sa.Column("description", sa.Text(), nullable=False, server_default="")
            )
        if "last_used_at" not in existing:
            batch.add_column(
                sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True)
            )
    _create_index_once("ix_credentials_workflow_id", "credentials", ["workflow_id"])
    _create_index_once(
        "ix_credentials_environment_id", "credentials", ["environment_id"]
    )
    _create_index_once(
        "ix_credentials_runner_pool_id", "credentials", ["runner_pool_id"]
    )

    existing = _columns("workflows")
    with op.batch_alter_table("workflows") as batch:
        if "draft_graph" not in existing:
            batch.add_column(sa.Column("draft_graph", sa.JSON(), nullable=True))
        if "published_version" not in existing:
            batch.add_column(
                sa.Column(
                    "published_version",
                    sa.Integer(),
                    nullable=False,
                    server_default="1",
                )
            )
        if "error_workflow_id" not in existing:
            batch.add_column(
                sa.Column(
                    "error_workflow_id",
                    sa.String(32),
                    nullable=True,
                )
            )
        if "error_alerts" not in existing:
            batch.add_column(
                sa.Column(
                    "error_alerts",
                    sa.JSON(),
                    nullable=False,
                    server_default="{}",
                )
            )
    _create_index_once(
        "ix_workflows_error_workflow_id", "workflows", ["error_workflow_id"]
    )

    existing = _columns("workflow_versions")
    with op.batch_alter_table("workflow_versions") as batch:
        if "notes" not in existing:
            batch.add_column(
                sa.Column("notes", sa.Text(), nullable=False, server_default="")
            )

    existing = _columns("deployments")
    with op.batch_alter_table("deployments") as batch:
        if "workflow_version_id" not in existing:
            batch.add_column(
                sa.Column(
                    "workflow_version_id",
                    sa.String(32),
                    nullable=True,
                )
            )
        if "error_workflow_id" not in existing:
            batch.add_column(
                sa.Column(
                    "error_workflow_id",
                    sa.String(32),
                    nullable=True,
                )
            )
        if "error_alerts" not in existing:
            batch.add_column(
                sa.Column(
                    "error_alerts",
                    sa.JSON(),
                    nullable=False,
                    server_default="{}",
                )
            )
    _create_index_once(
        "ix_deployments_workflow_version_id", "deployments", ["workflow_version_id"]
    )
    _create_index_once(
        "ix_deployments_error_workflow_id", "deployments", ["error_workflow_id"]
    )

    existing = _columns("runs")
    with op.batch_alter_table("runs") as batch:
        if "workflow_version_id" not in existing:
            batch.add_column(
                sa.Column(
                    "workflow_version_id",
                    sa.String(32),
                    nullable=True,
                )
            )
        if "deployment_id" not in existing:
            batch.add_column(
                sa.Column(
                    "deployment_id",
                    sa.String(32),
                    nullable=True,
                )
            )
        if "triggered_by_error_run_id" not in existing:
            batch.add_column(
                sa.Column(
                    "triggered_by_error_run_id",
                    sa.String(32),
                    nullable=True,
                )
            )
    _create_index_once("ix_runs_workflow_version_id", "runs", ["workflow_version_id"])
    _create_index_once("ix_runs_deployment_id", "runs", ["deployment_id"])
    _create_index_once(
        "ix_runs_triggered_by_error_run_id", "runs", ["triggered_by_error_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_runs_triggered_by_error_run_id", table_name="runs")
    op.drop_index("ix_runs_deployment_id", table_name="runs")
    op.drop_index("ix_runs_workflow_version_id", table_name="runs")
    op.drop_column("runs", "triggered_by_error_run_id")
    op.drop_column("runs", "deployment_id")
    op.drop_column("runs", "workflow_version_id")

    op.drop_index("ix_deployments_error_workflow_id", table_name="deployments")
    op.drop_index("ix_deployments_workflow_version_id", table_name="deployments")
    op.drop_column("deployments", "error_alerts")
    op.drop_column("deployments", "error_workflow_id")
    op.drop_column("deployments", "workflow_version_id")

    op.drop_column("workflow_versions", "notes")

    op.drop_index("ix_workflows_error_workflow_id", table_name="workflows")
    op.drop_column("workflows", "error_alerts")
    op.drop_column("workflows", "error_workflow_id")
    op.drop_column("workflows", "published_version")
    op.drop_column("workflows", "draft_graph")

    op.drop_index("ix_credentials_runner_pool_id", table_name="credentials")
    op.drop_index("ix_credentials_environment_id", table_name="credentials")
    op.drop_index("ix_credentials_workflow_id", table_name="credentials")
    op.drop_column("credentials", "last_used_at")
    op.drop_column("credentials", "description")
    op.drop_column("credentials", "runner_pool_id")
    op.drop_column("credentials", "environment_id")
    op.drop_column("credentials", "workflow_id")
    op.drop_column("credentials", "scope")
