"""add workflow folders

Revision ID: 0056_workflow_folders
Revises: 0055_uq_environment_is_global
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0056_workflow_folders"
down_revision: str | None = "0055_uq_environment_is_global"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("org_id", sa.String(32), nullable=False, server_default="default"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_folders_org_id", "folders", ["org_id"], unique=False)
    op.add_column(
        "workflows",
        sa.Column("folder_id", sa.String(32), nullable=True),
    )
    op.create_index("ix_workflows_folder_id", "workflows", ["folder_id"], unique=False)
    # SQLite doesn't support ALTER TABLE ADD CONSTRAINT — add the FK only on Postgres.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_workflows_folder_id",
            "workflows",
            "folders",
            ["folder_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_workflows_folder_id", "workflows", type_="foreignkey")
    op.drop_index("ix_workflows_folder_id", table_name="workflows")
    op.drop_column("workflows", "folder_id")
    op.drop_index("ix_folders_org_id", table_name="folders")
    op.drop_table("folders")
