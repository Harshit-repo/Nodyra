"""MCP connection call policy: enabled flag + tool allowlist.

Revision ID: 0082_mcp_connection_policy
Revises: 0081_artifact_checksum
"""

import sqlalchemy as sa
from alembic import op

revision = "0082_mcp_connection_policy"
down_revision = "0081_artifact_checksum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_connections",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("mcp_connections", sa.Column("allowed_tools", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_connections", "allowed_tools")
    op.drop_column("mcp_connections", "enabled")
