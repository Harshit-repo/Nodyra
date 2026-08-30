"""Add a transactional, retryable schedule occurrence outbox.

Revision ID: 0095_schedule_occurrence_outbox
Revises: 0094_execution_attempt_fencing
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0095_schedule_occurrence_outbox"
down_revision: str | None = "0094_execution_attempt_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ScheduleOccurrence.graph and .parameters are POSTGRES_JSON in the ORM,
    # which is JSONB on PostgreSQL. Plain sa.JSON here made the database
    # disagree with the model; SQLite treats them identically, so the
    # mismatch only surfaces under alembic check against PostgreSQL.
    if op.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        json_type = JSONB()
    else:
        json_type = sa.JSON()

    op.create_table(
        "schedule_occurrences",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.String(length=32), server_default="default", nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=32), nullable=True),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("deployment_id", sa.String(length=32), nullable=True),
        sa.Column("graph", json_type, nullable=False),
        sa.Column("trigger_node_id", sa.String(length=120), nullable=True),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("parameters", json_type, nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dispatch_token", sa.String(length=32), nullable=True),
        sa.Column("dispatch_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"], ["workflow_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "scheduled_for",
            name="uq_schedule_occurrences_source_time",
        ),
        sa.UniqueConstraint("run_id", name="uq_schedule_occurrences_run_id"),
    )
    op.create_index(
        "ix_schedule_occurrences_org_id", "schedule_occurrences", ["org_id"]
    )
    op.create_index(
        "ix_schedule_occurrences_workflow_id",
        "schedule_occurrences",
        ["workflow_id"],
    )
    op.create_index(
        "ix_schedule_occurrences_dispatch",
        "schedule_occurrences",
        ["status", "next_attempt_at", "created_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        predicate = "(NULLIF(current_setting('app.current_org', true), '') IS NULL " \
            "OR org_id = NULLIF(current_setting('app.current_org', true), ''))"
        op.execute("ALTER TABLE schedule_occurrences ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE schedule_occurrences FORCE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY org_isolation ON schedule_occurrences "
            f"USING {predicate} WITH CHECK {predicate}"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS org_isolation ON schedule_occurrences")
        op.execute("ALTER TABLE schedule_occurrences NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE schedule_occurrences DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_schedule_occurrences_dispatch", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_workflow_id", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_org_id", table_name="schedule_occurrences")
    op.drop_table("schedule_occurrences")
