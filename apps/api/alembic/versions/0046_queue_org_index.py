"""Phase C2 (multi-tenancy): composite index for org-fair queue leasing.

Revision ID: 0046_queue_org_index
Revises: 0045_org_settings
Create Date: 2026-06-10
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "0046_queue_org_index"
down_revision: str | None = "0045_org_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _indexes(table: str) -> set[str]:
    return {ix["name"] for ix in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "ix_run_queue_org_lease" not in _indexes("run_queue"):
        op.create_index(
            "ix_run_queue_org_lease",
            "run_queue",
            ["org_id", "status", "available_at"],
        )


def downgrade() -> None:
    if "ix_run_queue_org_lease" in _indexes("run_queue"):
        op.drop_index("ix_run_queue_org_lease", table_name="run_queue")
