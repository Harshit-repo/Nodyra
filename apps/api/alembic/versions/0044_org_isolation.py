"""X4 (multi-tenancy): per-org execution_isolation mode.

Revision ID: 0044_org_isolation
Revises: 0043_org_kek
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0044_org_isolation"
down_revision: str | None = "0043_org_kek"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "execution_isolation" not in _columns("organizations"):
        op.add_column(
            "organizations",
            sa.Column(
                "execution_isolation",
                sa.String(20),
                nullable=False,
                server_default="shared",
            ),
        )


def downgrade() -> None:
    if "execution_isolation" in _columns("organizations"):
        op.drop_column("organizations", "execution_isolation")
