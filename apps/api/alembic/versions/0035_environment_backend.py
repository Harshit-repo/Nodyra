"""environments: backend + backend_config columns

Revision ID: 0035_environment_backend
Revises: 0034_provider_triggers
Create Date: 2026-06-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_environment_backend"
down_revision: str | None = "0034_provider_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    existing = _columns("environments")
    with op.batch_alter_table("environments") as batch:
        if "backend" not in existing:
            batch.add_column(
                sa.Column("backend", sa.String(20), server_default="venv", nullable=False)
            )
        if "backend_config" not in existing:
            batch.add_column(
                sa.Column("backend_config", sa.JSON, server_default="{}", nullable=False)
            )


def downgrade() -> None:
    existing = _columns("environments")
    with op.batch_alter_table("environments") as batch:
        if "backend_config" in existing:
            batch.drop_column("backend_config")
        if "backend" in existing:
            batch.drop_column("backend")
