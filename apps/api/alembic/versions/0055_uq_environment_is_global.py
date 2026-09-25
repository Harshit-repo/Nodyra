"""add partial unique index enforcing at most one global environment

Revision ID: 0055_uq_environment_is_global
Revises: 0054_artifact_runner_org_id
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0055_uq_environment_is_global"
down_revision: str | None = "0054_artifact_runner_org_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_environment_is_global",
        "environments",
        ["org_id", "is_global"],
        unique=True,
        postgresql_where=sa.text("is_global IS TRUE"),
        sqlite_where=sa.text("is_global IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_environment_is_global", table_name="environments")
