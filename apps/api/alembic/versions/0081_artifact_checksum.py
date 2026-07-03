"""Add artifacts.checksum_sha256.

Revision ID: 0081_artifact_checksum
Revises: 0080_code_module_metadata
"""

import sqlalchemy as sa
from alembic import op

revision = "0081_artifact_checksum"
down_revision = "0080_code_module_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts", sa.Column("checksum_sha256", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("artifacts", "checksum_sha256")
