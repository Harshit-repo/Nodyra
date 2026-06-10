"""Phase C3 (multi-tenancy): per-org daily run metering table.

Revision ID: 0047_run_meters
Revises: 0046_queue_org_index
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0047_run_meters"
down_revision: str | None = "0046_queue_org_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "run_meters" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "run_meters",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(32),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "compute_seconds", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("node_runs", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("org_id", "day", name="uq_run_meters_org_day"),
    )
    op.create_index("ix_run_meters_org_id", "run_meters", ["org_id"])
    if op.get_bind().dialect.name == "postgresql":
        guc = "NULLIF(current_setting('app.current_org', true), '')"
        predicate = f"({guc} IS NULL OR org_id = {guc})"
        op.execute("ALTER TABLE run_meters ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE run_meters FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON run_meters "
            f"USING {predicate} WITH CHECK {predicate}"
        )


def downgrade() -> None:
    if "run_meters" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("run_meters")
