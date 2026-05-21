"""environments

Revision ID: 0003_environments
Revises: 0002_workflows
Create Date: 2026-05-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_environments"
down_revision: str | None = "0002_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "is_global", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "python_version", sa.String(16), nullable=False, server_default="3.12"
        ),
        sa.Column("packages", sa.JSON(), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column("status_detail", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "workflows", sa.Column("environment_id", sa.String(32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflows", "environment_id")
    op.drop_table("environments")
