# 0027_workflow_run_timeout.py
"""workflows.run_timeout_seconds column

Revision ID: 0027_workflow_run_timeout
Revises: 0026_credential_dek
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_workflow_run_timeout"
down_revision: str | None = "0026_credential_dek"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("run_timeout_seconds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflows", "run_timeout_seconds")
