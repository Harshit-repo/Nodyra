"""MCP production hardening: automation tokens and tenant tool names.

Revision ID: 0060_mcp_production_hardening
Revises: 0059_github_sync
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0060_mcp_production_hardening"
down_revision: str | None = "0059_github_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the newest explicit name and let older duplicates fall back to
    # their generated workflow_<slug>_<id> names before enforcing uniqueness.
    op.execute(
        "UPDATE workflows SET mcp_tool_name = NULL WHERE id IN ("
        "SELECT id FROM (SELECT id, ROW_NUMBER() OVER ("
        "PARTITION BY org_id, mcp_tool_name ORDER BY updated_at DESC, id"
        ") AS rn FROM workflows WHERE mcp_tool_name IS NOT NULL) ranked "
        "WHERE rn > 1)"
    )
    op.create_index(
        "uq_workflows_org_mcp_tool_name",
        "workflows",
        ["org_id", "mcp_tool_name"],
        unique=True,
    )

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("org_id", sa.String(32), nullable=False, server_default="default"),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(20), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
    )
    op.create_index("ix_api_tokens_org_id", "api_tokens", ["org_id"])
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])

    if op.get_bind().dialect.name == "postgresql":
        predicate = "(NULLIF(current_setting('app.current_org', true), '') IS NULL OR org_id = NULLIF(current_setting('app.current_org', true), ''))"
        op.execute("ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE api_tokens FORCE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY org_isolation ON api_tokens "
            f"USING {predicate} WITH CHECK {predicate}"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS org_isolation ON api_tokens")
        op.execute("ALTER TABLE api_tokens NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE api_tokens DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_index("ix_api_tokens_org_id", table_name="api_tokens")
    op.drop_table("api_tokens")
    op.drop_index("uq_workflows_org_mcp_tool_name", table_name="workflows")
