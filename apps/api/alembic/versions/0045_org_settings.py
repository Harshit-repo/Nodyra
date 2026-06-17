"""Phase C (multi-tenancy): per-org quota overrides table.

Revision ID: 0045_org_settings
Revises: 0044_org_isolation
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0045_org_settings"
down_revision: str | None = "0044_org_isolation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "org_settings" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "org_settings",
        sa.Column(
            "org_id",
            sa.String(32),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=True),
        sa.Column("executions_per_day", sa.Integer(), nullable=True),
        sa.Column("max_map_width", sa.Integer(), nullable=True),
        sa.Column("max_loop_iterations", sa.Integer(), nullable=True),
        sa.Column("max_inflight_subworkflows", sa.Integer(), nullable=True),
        sa.Column("storage_quota_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # RLS to match the other org-scoped tables (PK is the org id).
    if op.get_bind().dialect.name == "postgresql":
        guc = "NULLIF(current_setting('app.current_org', true), '')"
        predicate = f"({guc} IS NULL OR org_id = {guc})"
        op.execute("ALTER TABLE org_settings ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE org_settings FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON org_settings "
            f"USING {predicate} WITH CHECK {predicate}"
        )


def downgrade() -> None:
    if "org_settings" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("org_settings")
