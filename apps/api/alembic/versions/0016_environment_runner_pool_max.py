"""environment runner_pool_max + worker_rss_estimate + workspace memory budget

Revision ID: 0016_environment_runner_pool_max
Revises: 0015_env_and_system_settings
Create Date: 2026-05-26

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0016_environment_runner_pool_max"
down_revision: str | None = "0015_env_and_system_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    env_cols = _columns("environments")
    with op.batch_alter_table("environments") as batch:
        if "runner_pool_max" not in env_cols:
            batch.add_column(
                sa.Column("runner_pool_max", sa.Integer(), nullable=True)
            )
        if "worker_rss_estimate_bytes" not in env_cols:
            batch.add_column(
                sa.Column(
                    "worker_rss_estimate_bytes", sa.BigInteger(), nullable=True
                )
            )

    settings_cols = _columns("system_settings")
    with op.batch_alter_table("system_settings") as batch:
        if "worker_rss_soft_budget_bytes" not in settings_cols:
            batch.add_column(
                sa.Column(
                    "worker_rss_soft_budget_bytes",
                    sa.BigInteger(),
                    nullable=False,
                    server_default="0",
                )
            )


def downgrade() -> None:
    settings_cols = _columns("system_settings")
    with op.batch_alter_table("system_settings") as batch:
        if "worker_rss_soft_budget_bytes" in settings_cols:
            batch.drop_column("worker_rss_soft_budget_bytes")

    env_cols = _columns("environments")
    with op.batch_alter_table("environments") as batch:
        if "worker_rss_estimate_bytes" in env_cols:
            batch.drop_column("worker_rss_estimate_bytes")
        if "runner_pool_max" in env_cols:
            batch.drop_column("runner_pool_max")
