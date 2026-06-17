"""add sessions_valid_after to users (C1 session revocation)

Revision ID: 0053_user_sessions_valid_after
Revises: 0052_deployment_env_fk_ondelete
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0053_user_sessions_valid_after"
down_revision: str | None = "0052_deployment_env_fk_ondelete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("sessions_valid_after", sa.Float(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("sessions_valid_after")
