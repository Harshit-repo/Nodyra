"""Fence execution attempts and make outcome rows idempotent.

Revision ID: 0094_execution_attempt_fencing
Revises: 0093_rls_coverage_gap
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0094_execution_attempt_fencing"
down_revision: str | None = "0093_rls_coverage_gap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("run_queue") as batch:
        batch.add_column(sa.Column("lease_token", sa.String(length=32), nullable=True))

    with op.batch_alter_table("node_runs") as batch:
        batch.add_column(sa.Column("attempt_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("iteration_key", sa.String(length=64), nullable=True))
    # Preserve every legacy row even when an affected installation already
    # contains duplicates from retrying terminal persistence. Each legacy row
    # receives its own attempt/key identity before uniqueness is enforced.
    op.execute(sa.text("UPDATE node_runs SET attempt_id = id WHERE attempt_id IS NULL"))
    op.execute(sa.text("UPDATE node_runs SET iteration_key = id WHERE iteration_key IS NULL"))
    with op.batch_alter_table("node_runs") as batch:
        batch.alter_column(
            "attempt_id",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="legacy",
        )
        batch.alter_column(
            "iteration_key",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default="",
        )
        batch.create_unique_constraint(
            "uq_node_runs_attempt_node_iteration",
            ["run_id", "attempt_id", "node_id", "iteration_key"],
        )

    with op.batch_alter_table("run_events") as batch:
        batch.add_column(sa.Column("attempt_id", sa.String(length=32), nullable=True))
    op.execute(sa.text("UPDATE run_events SET attempt_id = id WHERE attempt_id IS NULL"))
    with op.batch_alter_table("run_events") as batch:
        batch.alter_column(
            "attempt_id",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="legacy",
        )
        batch.create_unique_constraint(
            "uq_run_events_attempt_sequence",
            ["run_id", "attempt_id", "sequence"],
        )


def downgrade() -> None:
    with op.batch_alter_table("run_events") as batch:
        batch.drop_constraint("uq_run_events_attempt_sequence", type_="unique")
        batch.drop_column("attempt_id")
    with op.batch_alter_table("node_runs") as batch:
        batch.drop_constraint("uq_node_runs_attempt_node_iteration", type_="unique")
        batch.drop_column("iteration_key")
        batch.drop_column("attempt_id")
    with op.batch_alter_table("run_queue") as batch:
        batch.drop_column("lease_token")
