"""add color to folders

Revision ID: 0057_folder_color
Revises: 0056_workflow_folders
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0057_folder_color"
down_revision: str | None = "0056_workflow_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("folders", sa.Column("color", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("folders", "color")
