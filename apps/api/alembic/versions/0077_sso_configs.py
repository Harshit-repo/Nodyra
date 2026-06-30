"""Add sso_configs table and user.sso_subject column

Revision ID: 0077_sso_configs
Revises: 0076_audit_log_actor
Create Date: 2026-06-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077_sso_configs"
down_revision: str | None = "0076_audit_log_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUC = "NULLIF(current_setting('app.current_org', true), '')"
_PREDICATE = f"({_GUC} IS NULL OR org_id = {_GUC})"


def upgrade() -> None:
    op.create_table(
        "sso_configs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("protocol", sa.Text(), nullable=False, server_default=sa.text("'oidc'")),
        sa.Column("client_id", sa.Text(), nullable=True),
        sa.Column("client_secret", sa.Text(), nullable=True),
        sa.Column("discovery_url", sa.Text(), nullable=True),
        sa.Column("idp_entity_id", sa.Text(), nullable=True),
        sa.Column("idp_sso_url", sa.Text(), nullable=True),
        sa.Column("idp_certificate", sa.Text(), nullable=True),
        sa.Column("email_domain", sa.Text(), nullable=True),
        sa.Column("attribute_map", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("jit_provisioning", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id"),
    )
    op.create_index(
        "ix_sso_configs_email_domain",
        "sso_configs",
        ["email_domain"],
        unique=True,
        postgresql_where=sa.text("email_domain IS NOT NULL"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE sso_configs ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE sso_configs FORCE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY sso_configs_org ON sso_configs "
            f"USING {_PREDICATE} WITH CHECK {_PREDICATE}"
        )

    # Add sso_subject column to users
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("sso_subject", sa.Text(), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS sso_configs_org ON sso_configs")
        op.execute("ALTER TABLE sso_configs NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE sso_configs DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_sso_configs_email_domain", table_name="sso_configs")
    op.drop_table("sso_configs")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("sso_subject")
