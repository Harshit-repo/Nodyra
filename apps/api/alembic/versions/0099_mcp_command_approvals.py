"""Bind human MCP approvals to actor, command, arguments and workflow revision.

Revision ID: 0099_mcp_command_approvals
Revises: 0098_mcp_gateway
"""

import sqlalchemy as sa
from alembic import op

revision = "0099_mcp_command_approvals"
down_revision = "0098_mcp_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_command_approvals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(32),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            server_default="default",
        ),
        sa.Column(
            "actor_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("principal_hash", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(160), nullable=False),
        sa.Column("permission", sa.String(80), nullable=False),
        sa.Column("arguments_digest", sa.String(64), nullable=False),
        sa.Column("arguments_preview", sa.JSON(), nullable=False),
        sa.Column("target_snapshot", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_mcp_approvals_org_actor", "mcp_command_approvals", ["org_id", "actor_id"])
    if op.get_bind().dialect.name == "postgresql":
        predicate = "(NULLIF(current_setting('app.current_org', true), '') IS NULL OR org_id = NULLIF(current_setting('app.current_org', true), ''))"
        op.execute("ALTER TABLE mcp_command_approvals ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE mcp_command_approvals FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON mcp_command_approvals USING {predicate} WITH CHECK {predicate}"
        )


def downgrade() -> None:
    op.drop_table("mcp_command_approvals")
