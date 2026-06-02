# 0029_environment_runner_pool.py
"""environments.runner_pool_id binding

Revision ID: 0029_environment_runner_pool
Revises: 0028_code_module_undecorated
Create Date: 2026-06-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_environment_runner_pool"
down_revision: str | None = "0028_code_module_undecorated"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if "runner_pool_id" in _columns("environments"):
        return
    with op.batch_alter_table("environments") as batch:
        batch.add_column(
            sa.Column("runner_pool_id", sa.String(length=32), nullable=True)
        )
        batch.create_index("ix_environments_runner_pool_id", ["runner_pool_id"])
        batch.create_foreign_key(
            "fk_environments_runner_pool_id",
            "runner_pools",
            ["runner_pool_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if "runner_pool_id" not in _columns("environments"):
        return
    with op.batch_alter_table("environments") as batch:
        batch.drop_constraint("fk_environments_runner_pool_id", type_="foreignkey")
        batch.drop_index("ix_environments_runner_pool_id")
        batch.drop_column("runner_pool_id")
