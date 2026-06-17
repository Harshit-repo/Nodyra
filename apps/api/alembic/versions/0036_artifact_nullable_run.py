"""artifacts: make run_id and node_id nullable for browser-upload artifacts

Revision ID: 0036_artifact_nullable_run
Revises: 0035_environment_backend
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_artifact_nullable_run"
down_revision: str | None = "0035_environment_backend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.alter_column("run_id", existing_type=sa.String(32), nullable=True)
        batch.alter_column("node_id", existing_type=sa.String(120), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.alter_column("run_id", existing_type=sa.String(32), nullable=False)
        batch.alter_column("node_id", existing_type=sa.String(120), nullable=False)
