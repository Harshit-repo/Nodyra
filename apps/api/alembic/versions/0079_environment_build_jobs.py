"""Add durable environment build jobs

Revision ID: 0079_environment_build_jobs
Revises: 0078_workflow_graph_revision
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0079_environment_build_jobs"
down_revision: str | None = "0078_workflow_graph_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environment_build_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=32),
            server_default="default",
            nullable=False,
        ),
        sa.Column("environment_id", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False, server_default="rebuild"),
        sa.Column("packages_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("package_snapshot", sa.JSON(), nullable=False),
        sa.Column("python_version", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("backend", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("backend_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempts_log", sa.JSON(), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("requested_by_email", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_environment_build_jobs_environment_id",
        "environment_build_jobs",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_environment_build_jobs_org_id",
        "environment_build_jobs",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_environment_build_jobs_status_available",
        "environment_build_jobs",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_environment_build_jobs_status_lease",
        "environment_build_jobs",
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_environment_build_jobs_env_status",
        "environment_build_jobs",
        ["environment_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_environment_build_jobs_org_status",
        "environment_build_jobs",
        ["org_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_environment_build_jobs_org_status", table_name="environment_build_jobs")
    op.drop_index("ix_environment_build_jobs_env_status", table_name="environment_build_jobs")
    op.drop_index("ix_environment_build_jobs_status_lease", table_name="environment_build_jobs")
    op.drop_index("ix_environment_build_jobs_status_available", table_name="environment_build_jobs")
    op.drop_index("ix_environment_build_jobs_org_id", table_name="environment_build_jobs")
    op.drop_index("ix_environment_build_jobs_environment_id", table_name="environment_build_jobs")
    op.drop_table("environment_build_jobs")
