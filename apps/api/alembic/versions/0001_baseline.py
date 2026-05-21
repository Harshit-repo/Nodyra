"""baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-19

"""
from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Empty baseline. Schema tables are introduced by later milestone migrations."""


def downgrade() -> None:
    pass
