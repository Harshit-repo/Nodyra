# 0031_run_webhook_response.py
"""runs webhook_response column

Records the synchronous response a webhook run should return to its caller
(Respond Node / Last Node modes). Nullable JSON, additive and forward-only.

Revision ID: 0031_run_webhook_response
Revises: 0030_run_queue_lease_indexes
Create Date: 2026-06-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_run_webhook_response"
down_revision: str | None = "0030_run_queue_lease_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("webhook_response", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "webhook_response")
