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
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    perms_default = sa.text("'[]'::json") if is_pg else sa.text("'[]'")

    if "custom_roles" not in existing_tables:
        op.create_table(
            "custom_roles",
            sa.Column("id", sa.String(32), primary_key=True, default=sa.text("gen_ulid()")),
            sa.Column("org_id", sa.String(32), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("permissions", sa.JSON, nullable=False, server_default=perms_default),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("uq_custom_roles_org_name", "custom_roles", ["org_id", "name"], unique=True)
        op.create_index("ix_custom_roles_org_id", "custom_roles", ["org_id"])

    existing_cols = {c["name"] for c in inspector.get_columns("memberships")}
    if "custom_role_id" not in existing_cols:
        with op.batch_alter_table("memberships") as batch_op:
            batch_op.add_column(
                sa.Column("custom_role_id", sa.String(32), nullable=True)
            )
            if is_pg:
                batch_op.create_foreign_key(
                    "fk_memberships_custom_role_id",
                    "custom_roles",
                    ["custom_role_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            batch_op.create_index("ix_memberships_custom_role_id", ["custom_role_id"])

    # RLS: custom_roles holds tenant data — must be org-scoped.
    if is_pg:
        op.execute("ALTER TABLE custom_roles ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE custom_roles FORCE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY custom_roles_org ON custom_roles "
            f"USING {_PREDICATE} WITH CHECK {_PREDICATE}"
        )


def downgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    if is_pg:
        op.execute("DROP POLICY IF EXISTS custom_roles_org ON custom_roles")
        op.execute("ALTER TABLE custom_roles NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE custom_roles DISABLE ROW LEVEL SECURITY")
    with op.batch_alter_table("memberships") as batch_op:
        batch_op.drop_index("ix_memberships_custom_role_id")
        if is_pg:
            batch_op.drop_constraint("fk_memberships_custom_role_id", type_="foreignkey")
        batch_op.drop_column("custom_role_id")
    op.drop_index("uq_custom_roles_org_name", table_name="custom_roles")
    op.drop_index("ix_custom_roles_org_id", table_name="custom_roles")
    op.drop_table("custom_roles")
