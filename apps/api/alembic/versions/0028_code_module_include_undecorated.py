# 0028_code_module_include_undecorated.py
"""code_modules.include_undecorated column

Revision ID: 0028_code_module_undecorated
Revises: 0027_workflow_run_timeout
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_code_module_undecorated"
down_revision: str | None = "0027_workflow_run_timeout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "code_modules",
        sa.Column(
            "include_undecorated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("code_modules", "include_undecorated")
