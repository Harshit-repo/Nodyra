"""artifacts: durable run files outside NodeRun.output

Revision ID: 0012_artifacts
Revises: 0011_code_modules
Create Date: 2026-05-25

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_artifacts"
down_revision: str | None = "0011_code_modules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(32),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False, server_default="binary"),
        sa.Column(
            "content_type",
            sa.String(160),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "storage_backend",
            sa.String(40),
            nullable=False,
            server_default="local",
        ),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("preview", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifacts_node_id", "artifacts", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_node_id", table_name="artifacts")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_table("artifacts")
