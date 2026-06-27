"""Runner production hardening: token_expires_at, aws_secret_key_enc, required_labels, history index

Revision ID: 0068_runners_hardening
Revises: 0067_node_run_dt_audit_actor_id
Create Date: 2026-06-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision: str = "0068_runners_hardening"
down_revision: str | None = "0067_node_run_dt_audit_actor_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    runner_cols = _columns("runners")
    with op.batch_alter_table("runners") as batch:
        if "token_expires_at" not in runner_cols:
            batch.add_column(
                sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True)
            )

    pool_cols = _columns("runner_pools")
    with op.batch_alter_table("runner_pools") as batch:
        if "aws_secret_key_enc" not in pool_cols:
            batch.add_column(
                sa.Column("aws_secret_key_enc", sa.Text(), nullable=True)
            )

    run_cols = _columns("runs")
    with op.batch_alter_table("runs") as batch:
        if "required_labels" not in run_cols:
            batch.add_column(
                sa.Column("required_labels", sa.JSON(), nullable=True)
            )

    # Composite index for run-history bucketing queries
    idx_name = "ix_runs_runner_pool_finished"
    if idx_name not in _indexes("runs"):
        op.create_index(
            idx_name,
            "runs",
            ["runner_pool_id", "finished_at"],
        )

    # Data migration: move aws_secret_access_key out of provider_config JSON.
    # Stores a sentinel prefix so the router re-encrypts on next write.
    # Fernet key may not be available in the migration environment.
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, provider_config FROM runner_pools WHERE provider_config IS NOT NULL")
    ).fetchall()
    for row_id, cfg in rows:
        if not isinstance(cfg, dict):
            continue
        secret = cfg.get("aws_secret_access_key")
        if not secret:
            continue
        cleaned = {k: v for k, v in cfg.items() if k != "aws_secret_access_key"}
        import json
        conn.execute(
            text(
                "UPDATE runner_pools SET provider_config = :cfg, aws_secret_key_enc = :enc "
                "WHERE id = :id"
            ),
            {"cfg": json.dumps(cleaned), "enc": f"__migrated__{secret}", "id": row_id},
        )


def downgrade() -> None:
    idx_name = "ix_runs_runner_pool_finished"
    if idx_name in _indexes("runs"):
        op.drop_index(idx_name, table_name="runs")

    run_cols = _columns("runs")
    with op.batch_alter_table("runs") as batch:
        if "required_labels" in run_cols:
            batch.drop_column("required_labels")

    pool_cols = _columns("runner_pools")
    with op.batch_alter_table("runner_pools") as batch:
        if "aws_secret_key_enc" in pool_cols:
            batch.drop_column("aws_secret_key_enc")

    runner_cols = _columns("runners")
    with op.batch_alter_table("runners") as batch:
        if "token_expires_at" in runner_cols:
            batch.drop_column("token_expires_at")
