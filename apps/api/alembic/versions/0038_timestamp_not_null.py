"""run_queue + provider_trigger_subscriptions: created_at/updated_at NOT NULL

ALM-1: migrations 0020 and 0034 created these timestamp columns with a
``server_default`` but omitted ``nullable=False``, so the database allowed NULL
while the ORM models declare them NOT NULL (``Mapped[datetime]``). In practice
they are never NULL (the server_default fills them), so this purely aligns the
DB schema with the models — safe because the existing default guarantees no NULL
rows. Uses batch mode so SQLite (which can't ALTER in place) also works.

Revision ID: 0038_timestamp_not_null
Revises: 0037_node_run_iteration
Create Date: 2026-06-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_timestamp_not_null"
down_revision: str | None = "0037_node_run_iteration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("run_queue", "provider_trigger_subscriptions")
_COLS = ("created_at", "updated_at")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            for col in _COLS:
                batch.alter_column(
                    col,
                    existing_type=sa.DateTime(timezone=True),
                    existing_server_default=sa.func.now(),
                    nullable=False,
                )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            for col in _COLS:
                batch.alter_column(
                    col,
                    existing_type=sa.DateTime(timezone=True),
                    existing_server_default=sa.func.now(),
                    nullable=True,
                )
