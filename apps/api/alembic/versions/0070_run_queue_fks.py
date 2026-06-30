"""Add missing foreign keys on run_queue (workflow_id, environment_id, runner_pool_id).

Revision ID: 0070
Revises: 0069
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.create_foreign_key(
        "fk_run_queue_workflow_id",
        "run_queue",
        "workflows",
        ["workflow_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_run_queue_environment_id",
        "run_queue",
        "environments",
        ["environment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_run_queue_runner_pool_id",
        "run_queue",
        "runner_pools",
        ["runner_pool_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_constraint("fk_run_queue_runner_pool_id", "run_queue", type_="foreignkey")
    op.drop_constraint("fk_run_queue_environment_id", "run_queue", type_="foreignkey")
    op.drop_constraint("fk_run_queue_workflow_id", "run_queue", type_="foreignkey")
