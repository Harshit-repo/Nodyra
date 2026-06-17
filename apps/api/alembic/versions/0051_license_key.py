"""add license_key to system_settings

Revision ID: 0051_license_key
Revises: 0050_workflow_mcp
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0051_license_key"
down_revision: str | None = "0050_workflow_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("system_settings") as batch:
        batch.add_column(sa.Column("license_key", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("system_settings") as batch:
        batch.drop_column("license_key")
