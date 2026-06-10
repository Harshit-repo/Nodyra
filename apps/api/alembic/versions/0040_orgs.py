"""Phase A1 (multi-tenancy): organizations + memberships, default-org backfill.

Creates the two tenancy identity tables and seeds the single "default"
organization that every tenant-owned table will point at (migration 0041 adds
the org_id columns). Every existing user gets a membership in the default org
carrying their current global ``users.role`` — ``User.role`` itself stays in
place as the fallback while ``multi_tenancy_enabled`` is off (dropped in a
later Phase B migration).

Revision ID: 0040_orgs
Revises: 0039_fk_and_index_alignment
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0040_orgs"
down_revision: str | None = "0039_fk_and_index_alignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ORG_ID = "default"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "organizations" not in _tables():
        op.create_table(
            "organizations",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("slug", sa.String(80), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("wrapped_org_kek", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_organizations_slug", "organizations", ["slug"], unique=True
        )

    if "memberships" not in _tables():
        op.create_table(
            "memberships",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "org_id",
                sa.String(32),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(32),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("org_id", "user_id", name="uq_membership_org_user"),
        )
        op.create_index("ix_memberships_org_id", "memberships", ["org_id"])
        op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    bind = op.get_bind()
    # Explicit casts: asyncpg refuses to deduce a type for a parameter used
    # both in the SELECT list and a varchar comparison ("inconsistent types
    # deduced for parameter $1") — caught by the Postgres lane, invisible to
    # SQLite.
    bind.execute(
        sa.text(
            "INSERT INTO organizations (id, name, slug, status) "
            "SELECT CAST(:id AS VARCHAR(32)), 'Default', "
            "CAST(:slug AS VARCHAR(80)), 'active' "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM organizations WHERE id = CAST(:probe AS VARCHAR(32))"
            ")"
        ),
        {"id": DEFAULT_ORG_ID, "slug": DEFAULT_ORG_ID, "probe": DEFAULT_ORG_ID},
    )
    # One membership per existing user, carrying their global role into the
    # default org. hex-uuid ids are generated app-side normally; here we reuse
    # the user id as the membership id — both are 32-char ids, the column is a
    # plain string PK, and it makes the backfill idempotent.
    bind.execute(
        sa.text(
            "INSERT INTO memberships (id, org_id, user_id, role) "
            "SELECT u.id, CAST(:org AS VARCHAR(32)), u.id, u.role FROM users u "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM memberships m "
            "  WHERE m.org_id = CAST(:probe AS VARCHAR(32)) AND m.user_id = u.id"
            ")"
        ),
        {"org": DEFAULT_ORG_ID, "probe": DEFAULT_ORG_ID},
    )


def downgrade() -> None:
    if "memberships" in _tables():
        op.drop_table("memberships")
    if "organizations" in _tables():
        op.drop_table("organizations")
