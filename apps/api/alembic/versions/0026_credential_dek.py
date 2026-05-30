"""Add encrypted_dek column to credentials for per-credential DEK encryption.

Revision ID: 0026_credential_dek
Revises: 0025_run_dedup_key
Create Date: 2026-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_credential_dek"
down_revision: str | None = "0025_run_dedup_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column("encrypted_dek", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("credentials", "encrypted_dek")
