"""Add mcp_connections table.

Revision ID: 0074
Revises: 0073
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_connections",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "transport",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'streamable-http'"),
        ),
        sa.Column(
            "auth_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column("auth_secret", sa.Text(), nullable=True),
        sa.Column(
            "headers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("tool_cache", postgresql.JSONB(), nullable=True),
        sa.Column(
            "last_synced_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_mcp_connections_org", "mcp_connections", ["org_id"])
    op.execute("ALTER TABLE mcp_connections ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mcp_connections_org ON mcp_connections "
        "USING (org_id = current_setting('app.org_id', true))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS mcp_connections_org ON mcp_connections"
    )
    op.execute("ALTER TABLE mcp_connections DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_mcp_connections_org", table_name="mcp_connections")
    op.drop_table("mcp_connections")
