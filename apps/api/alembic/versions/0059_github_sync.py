"""add github sync tables and workflow columns

Revision ID: 0059_github_sync
Revises: 0058_tenant_rls_hardening
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0059_github_sync"
down_revision: str | None = "0058_tenant_rls_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_sync_configs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("org_id", sa.String(32), nullable=False, server_default="default"),
        sa.Column("credential_id", sa.String(32), nullable=True),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("base_path", sa.String(200), nullable=False, server_default="workflows/"),
        sa.Column("main_branch", sa.String(100), nullable=False, server_default="main"),
        sa.Column("webhook_secret", sa.Text, nullable=False),
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
        sa.UniqueConstraint("org_id", name="uq_github_sync_configs_org_id"),
    )
    op.create_index("ix_github_sync_configs_org_id", "github_sync_configs", ["org_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_github_sync_configs_credential_id",
            "github_sync_configs",
            "credentials",
            ["credential_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "github_sync_jobs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("org_id", sa.String(32), nullable=False, server_default="default"),
        sa.Column("workflow_id", sa.String(32), nullable=True),
        sa.Column("job_type", sa.String(20), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False, server_default="ui"),
        sa.Column("file_sha", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_github_sync_jobs_org_id", "github_sync_jobs", ["org_id"])
    op.create_index("ix_github_sync_jobs_workflow_id", "github_sync_jobs", ["workflow_id"])
    op.create_index(
        "ix_github_sync_jobs_status_next_retry",
        "github_sync_jobs",
        ["status", "next_retry_at"],
    )

    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_github_sync_jobs_workflow_id",
            "github_sync_jobs",
            "workflows",
            ["workflow_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.add_column("workflows", sa.Column("github_sync_sha", sa.String(40), nullable=True))
    op.add_column("workflows", sa.Column("github_sync_status", sa.String(20), nullable=True))
    op.add_column(
        "workflows", sa.Column("github_sync_conflict_sha", sa.String(40), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflows", "github_sync_conflict_sha")
    op.drop_column("workflows", "github_sync_status")
    op.drop_column("workflows", "github_sync_sha")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "fk_github_sync_jobs_workflow_id", "github_sync_jobs", type_="foreignkey"
        )
    op.drop_index("ix_github_sync_jobs_status_next_retry", "github_sync_jobs")
    op.drop_index("ix_github_sync_jobs_workflow_id", "github_sync_jobs")
    op.drop_index("ix_github_sync_jobs_org_id", "github_sync_jobs")
    op.drop_table("github_sync_jobs")

    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "fk_github_sync_configs_credential_id",
            "github_sync_configs",
            type_="foreignkey",
        )
    op.drop_index("ix_github_sync_configs_org_id", "github_sync_configs")
    op.drop_table("github_sync_configs")
