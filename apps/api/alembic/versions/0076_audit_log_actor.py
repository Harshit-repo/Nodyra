"""audit_logs session_id + actor_type columns

Revision ID: 0076_audit_log_actor
Revises: 0075_custom_roles
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076_audit_log_actor"
down_revision: str | None = "0075_custom_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.add_column(
            sa.Column("session_id", sa.String(64), nullable=True),
        )
        batch_op.add_column(
            sa.Column("actor_type", sa.String(20), nullable=False, server_default="user"),
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_column("actor_type")
        batch_op.drop_column("session_id")
