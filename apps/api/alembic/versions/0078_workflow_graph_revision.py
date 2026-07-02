"""Add workflow graph revision

Revision ID: 0078_workflow_graph_revision
Revises: 0077_sso_configs
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_workflow_graph_revision"
down_revision: str | None = "0077_sso_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflows") as batch_op:
        batch_op.add_column(
            sa.Column(
                "graph_revision",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    op.create_table(
        "workflow_revisions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=32),
            server_default="default",
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("graph_revision", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("patch", sa.JSON(), nullable=True),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_id",
            "graph_revision",
            name="uq_workflow_revisions_workflow_revision",
        ),
    )
    op.create_index(
        "ix_workflow_revisions_org_id",
        "workflow_revisions",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_revisions_workflow_id",
        "workflow_revisions",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_revisions_org_workflow_created",
        "workflow_revisions",
        ["org_id", "workflow_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_revisions_org_workflow_created",
        table_name="workflow_revisions",
    )
    op.drop_index("ix_workflow_revisions_workflow_id", table_name="workflow_revisions")
    op.drop_index("ix_workflow_revisions_org_id", table_name="workflow_revisions")
    op.drop_table("workflow_revisions")
    with op.batch_alter_table("workflows") as batch_op:
        batch_op.drop_column("graph_revision")
