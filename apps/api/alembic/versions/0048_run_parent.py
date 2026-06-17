"""A3 (architecture program Phase 4): runs.parent_run_id for sub-workflow children.

Set when a run is a sub-workflow child spawned by another run's
execute_workflow / map_* node (mode="subworkflow"). The FK is added on
non-SQLite only, matching 0039: SQLite can't ADD CONSTRAINT without
rebuilding the self-referential runs table, and the test schema is built
via create_all (which already has the FK from the model).

Revision ID: 0048_run_parent
Revises: 0047_run_meters
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_run_parent"
down_revision: str | None = "0047_run_meters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("parent_run_id", sa.String(32), nullable=True))
    op.create_index("ix_runs_parent_run_id", "runs", ["parent_run_id"])
    if op.get_bind().dialect.name != "sqlite":
        op.execute(
            "UPDATE runs SET parent_run_id = NULL WHERE parent_run_id IS NOT NULL "
            "AND parent_run_id NOT IN (SELECT id FROM runs)"
        )
        op.create_foreign_key(
            "fk_runs_parent_run_id", "runs", "runs",
            ["parent_run_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_runs_parent_run_id", "runs", type_="foreignkey")
    op.drop_index("ix_runs_parent_run_id", table_name="runs")
    op.drop_column("runs", "parent_run_id")
