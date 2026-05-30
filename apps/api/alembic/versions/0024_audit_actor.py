# 0024_audit_actor.py
"""audit_events actor attribution columns

Revision ID: 0024_audit_actor
Revises: 0023_workflow_allow_concurrent
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_audit_actor"
down_revision: str | None = "0023_workflow_allow_concurrent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("actor_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("actor_email", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_events", "actor_email")
    op.drop_column("audit_events", "actor_id")
