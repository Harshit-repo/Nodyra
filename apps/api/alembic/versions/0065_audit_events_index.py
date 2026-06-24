"""Add composite index on audit_events (org_id, created_at DESC) (D-11)

Without this index, activity-log queries under multi-tenancy scan the full
audit_events table. The (org_id, created_at DESC) index matches the query
pattern: filter by org, order newest-first, paginate.

Revision ID: 0065_audit_events_index
Revises: 0064_encrypt_webhook_secret
"""
from __future__ import annotations

from alembic import op

revision: str = "0065_audit_events_index"
down_revision: str | None = "0064_encrypt_webhook_secret"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_audit_events_org_id_created_at "
            "ON audit_events (org_id, created_at DESC)"
        )
    else:
        # SQLite does not support DESC in CREATE INDEX column list, but
        # still benefits from an org_id prefix index for the tenant filter.
        op.create_index(
            "ix_audit_events_org_id_created_at",
            "audit_events",
            ["org_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_audit_events_org_id_created_at")
    else:
        op.drop_index("ix_audit_events_org_id_created_at", table_name="audit_events")
