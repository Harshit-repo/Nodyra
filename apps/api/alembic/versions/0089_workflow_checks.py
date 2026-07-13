"""Add persisted workflow checks.

Revision ID: 0089_workflow_checks
Revises: 0088_environment_interpreter_flags
"""

import sqlalchemy as sa
from alembic import op

revision = "0089_workflow_checks"
down_revision = "0088_environment_interpreter_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_checks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(32),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            server_default="default",
        ),
        sa.Column(
            "workflow_id",
            sa.String(32),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("expected_outputs", sa.JSON(), nullable=False),
        sa.Column("assertions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="untested"),
        sa.Column("last_result", sa.JSON(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_workflow_checks_org_id", "workflow_checks", ["org_id"])
    op.create_index("ix_workflow_checks_workflow_id", "workflow_checks", ["workflow_id"])
    op.create_index(
        "ix_workflow_checks_org_workflow_created",
        "workflow_checks",
        ["org_id", "workflow_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_checks_org_workflow_created", table_name="workflow_checks")
    op.drop_index("ix_workflow_checks_workflow_id", table_name="workflow_checks")
    op.drop_index("ix_workflow_checks_org_id", table_name="workflow_checks")
    op.drop_table("workflow_checks")
