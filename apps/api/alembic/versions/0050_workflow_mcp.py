"""MCP server: per-workflow tool exposure columns.

``mcp_enabled`` opts the workflow into the /mcp tools list;
``mcp_tool_name`` / ``mcp_description`` / ``mcp_parameters_schema`` shape the
advertised tool. Additive and nullable/defaulted — no behaviour change while
off.

Revision ID: 0050_workflow_mcp
Revises: 0049_queue_trace_context
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_workflow_mcp"
down_revision: str | None = "0049_queue_trace_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "mcp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("workflows", sa.Column("mcp_tool_name", sa.String(64), nullable=True))
    op.add_column(
        "workflows", sa.Column("mcp_description", sa.String(500), nullable=True)
    )
    op.add_column(
        "workflows", sa.Column("mcp_parameters_schema", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflows", "mcp_parameters_schema")
    op.drop_column("workflows", "mcp_description")
    op.drop_column("workflows", "mcp_tool_name")
    op.drop_column("workflows", "mcp_enabled")
