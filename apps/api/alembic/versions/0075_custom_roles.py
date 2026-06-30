"""custom_roles table + memberships.custom_role_id FK

Revision ID: 0075_custom_roles
Revises: 0074
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0075_custom_roles"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUC = "NULLIF(current_setting('app.current_org', true), '')"
_PREDICATE = f"({_GUC} IS NULL OR org_id = {_GUC})"


def upgrade() -> None:
    op.create_table(
        "custom_roles",
        sa.Column("id", sa.String(32), primary_key=True, default=sa.text("gen_ulid()")),
        sa.Column("org_id", sa.String(32), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("permissions", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("uq_custom_roles_org_name", "custom_roles", ["org_id", "name"], unique=True)
    op.create_index("ix_custom_roles_org_id", "custom_roles", ["org_id"])

    op.add_column(
        "memberships",
        sa.Column(
            "custom_role_id",
            sa.String(32),
            sa.ForeignKey("custom_roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_memberships_custom_role_id", "memberships", ["custom_role_id"])

    # RLS: custom_roles holds tenant data — must be org-scoped.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE custom_roles ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE custom_roles FORCE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY custom_roles_org ON custom_roles "
            f"USING {_PREDICATE} WITH CHECK {_PREDICATE}"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS custom_roles_org ON custom_roles")
        op.execute("ALTER TABLE custom_roles NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE custom_roles DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_memberships_custom_role_id", table_name="memberships")
    op.drop_column("memberships", "custom_role_id")
    op.drop_index("uq_custom_roles_org_name", table_name="custom_roles")
    op.drop_index("ix_custom_roles_org_id", table_name="custom_roles")
    op.drop_table("custom_roles")
