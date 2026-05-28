"""Remote runners, run queueing, and parameter matrix batch runs

Revision ID: 0018_remote_runners
Revises: 0017_arch_fixes_indices
Create Date: 2026-05-28

Adds:
  * runner_pools  — agent / docker / kubernetes cloud-execution pools
  * runners       — agent runner instances connected to a pool
  * run_batches   — parameter-matrix batch job tracking
  * nullable FK columns on workflows, deployments, runs for pool routing
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0018_remote_runners"
down_revision: str | None = "0017_arch_fixes_indices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()

    if "runner_pools" not in existing:
        op.create_table(
            "runner_pools",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("provider", sa.String(20), nullable=False, server_default="agent"),
            sa.Column("provider_config", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="4"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if "runners" not in existing:
        op.create_table(
            "runners",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "pool_id",
                sa.String(32),
                sa.ForeignKey("runner_pools.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
            sa.Column("token_hash", sa.Text(), nullable=False, server_default=""),
            sa.Column("capabilities", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("current_runs", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("cached_env_ids", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if "run_batches" not in existing:
        op.create_table(
            "run_batches",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "workflow_id",
                sa.String(32),
                sa.ForeignKey("workflows.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("deployment_id", sa.String(32), nullable=True, index=True),
            sa.Column("runner_pool_id", sa.String(32), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="running"),
            sa.Column("total_runs", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("succeeded_runs", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_runs", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cancelled_runs", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("parameters", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Additive nullable FK columns on existing tables
    wf_cols = _columns("workflows")
    with op.batch_alter_table("workflows") as batch:
        if "default_runner_pool_id" not in wf_cols:
            batch.add_column(sa.Column("default_runner_pool_id", sa.String(32), nullable=True))

    dep_cols = _columns("deployments")
    with op.batch_alter_table("deployments") as batch:
        if "runner_pool_id" not in dep_cols:
            batch.add_column(sa.Column("runner_pool_id", sa.String(32), nullable=True))

    run_cols = _columns("runs")
    with op.batch_alter_table("runs") as batch:
        if "runner_pool_id" not in run_cols:
            batch.add_column(sa.Column("runner_pool_id", sa.String(32), nullable=True))
        if "runner_id" not in run_cols:
            batch.add_column(sa.Column("runner_id", sa.String(32), nullable=True))
        if "batch_id" not in run_cols:
            batch.add_column(sa.Column("batch_id", sa.String(32), nullable=True))
        if "queue_position" not in run_cols:
            batch.add_column(sa.Column("queue_position", sa.Integer(), nullable=True))


def downgrade() -> None:
    run_cols = _columns("runs")
    with op.batch_alter_table("runs") as batch:
        for col in ("queue_position", "batch_id", "runner_id", "runner_pool_id"):
            if col in run_cols:
                batch.drop_column(col)

    dep_cols = _columns("deployments")
    with op.batch_alter_table("deployments") as batch:
        if "runner_pool_id" in dep_cols:
            batch.drop_column("runner_pool_id")

    wf_cols = _columns("workflows")
    with op.batch_alter_table("workflows") as batch:
        if "default_runner_pool_id" in wf_cols:
            batch.drop_column("default_runner_pool_id")

    existing = _tables()
    if "run_batches" in existing:
        op.drop_table("run_batches")
    if "runners" in existing:
        op.drop_table("runners")
    if "runner_pools" in existing:
        op.drop_table("runner_pools")
