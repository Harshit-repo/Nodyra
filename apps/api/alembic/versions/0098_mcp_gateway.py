"""Approved MCP contracts, durable gateway outcomes, and run initiators."""

import sqlalchemy as sa
from alembic import op

revision = "0098_mcp_gateway"
down_revision = "0097_sqlite_partial_env_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_connections", sa.Column("gateway_policy", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("initiator_id", sa.String(32), nullable=True))
    op.add_column(
        "runs", sa.Column("initiator_kind", sa.String(20), nullable=False, server_default="system")
    )
    op.add_column("runs", sa.Column("execution_graph_digest", sa.String(64), nullable=True))
    op.create_table(
        "mcp_gateway_invocations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(32),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            server_default="default",
        ),
        sa.Column("connection_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(32), nullable=True),
        sa.Column("actor_kind", sa.String(20), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=True),
        sa.Column("workflow_id", sa.String(32), nullable=True),
        sa.Column("workflow_version_id", sa.String(32), nullable=True),
        sa.Column("workflow_version", sa.Integer(), nullable=True),
        sa.Column("graph_digest", sa.String(64), nullable=True),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("arguments_digest", sa.String(64), nullable=False),
        sa.Column("argument_keys", sa.JSON(), nullable=False),
        sa.Column("policy_revision", sa.String(32), nullable=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name in ("org_id", "connection_id", "run_id"):
        op.create_index(f"ix_mcp_gateway_invocations_{name}", "mcp_gateway_invocations", [name])
    if op.get_bind().dialect.name == "postgresql":
        predicate = "(NULLIF(current_setting('app.current_org', true), '') IS NULL OR org_id = NULLIF(current_setting('app.current_org', true), ''))"
        op.execute("ALTER TABLE mcp_gateway_invocations ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE mcp_gateway_invocations FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON mcp_gateway_invocations USING {predicate} WITH CHECK {predicate}"
        )


def downgrade() -> None:
    op.drop_table("mcp_gateway_invocations")
    op.drop_column("runs", "initiator_kind")
    op.drop_column("runs", "execution_graph_digest")
    op.drop_column("runs", "initiator_id")
    op.drop_column("mcp_connections", "gateway_policy")
