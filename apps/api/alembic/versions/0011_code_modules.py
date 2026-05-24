"""code_modules: user-uploaded Python files whose functions become nodes

Revision ID: 0011_code_modules
Revises: 0010_deployments
Create Date: 2026-05-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_code_modules"
down_revision: str | None = "0010_deployments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "code_modules",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(20), nullable=False, server_default="workflow"),
        sa.Column(
            "workflow_id",
            sa.String(32),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "environment_id",
            sa.String(32),
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("contents", sa.Text, nullable=False, server_default=""),
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
    op.create_index("ix_code_modules_workflow_id", "code_modules", ["workflow_id"])
    op.create_index(
        "ix_code_modules_environment_id", "code_modules", ["environment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_code_modules_environment_id", table_name="code_modules")
    op.drop_index("ix_code_modules_workflow_id", table_name="code_modules")
    op.drop_table("code_modules")
