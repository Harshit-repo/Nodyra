"""Add unique constraint on (workflow_id, version) in workflow_versions (D-02)

Without this, two concurrent version saves can produce duplicate (workflow_id, version)
pairs. Enforce at the DB level.

Revision ID: 0062_workflow_version_unique
Revises: 0061_runs_dedup_unique
"""
from __future__ import annotations

from alembic import op

revision: str = "0062_workflow_version_unique"
down_revision: str | None = "0061_runs_dedup_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_versions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_workflow_versions_workflow_version",
            ["workflow_id", "version"],
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_versions") as batch_op:
        batch_op.drop_constraint(
            "uq_workflow_versions_workflow_version", type_="unique"
        )
