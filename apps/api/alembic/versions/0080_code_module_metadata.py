"""Add metadata to code modules.

Revision ID: 0080_code_module_metadata
Revises: 0079_environment_build_jobs
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0080_code_module_metadata"
down_revision: str | None = "0079_environment_build_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "metadata" in _columns("code_modules"):
        return
    op.add_column(
        "code_modules",
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    if "metadata" not in _columns("code_modules"):
        return
    op.drop_column("code_modules", "metadata")
