"""user profile fields

Revision ID: 0014_user_profile_fields
Revises: 0013_production_slices
Create Date: 2026-05-26

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0014_user_profile_fields"
down_revision: str | None = "0013_production_slices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _columns("users")
    with op.batch_alter_table("users") as batch:
        if "name" not in existing:
            batch.add_column(
                sa.Column("name", sa.String(160), nullable=False, server_default="")
            )
        if "company" not in existing:
            batch.add_column(
                sa.Column("company", sa.String(160), nullable=False, server_default="")
            )


def downgrade() -> None:
    existing = _columns("users")
    with op.batch_alter_table("users") as batch:
        if "company" in existing:
            batch.drop_column("company")
        if "name" in existing:
            batch.drop_column("name")
