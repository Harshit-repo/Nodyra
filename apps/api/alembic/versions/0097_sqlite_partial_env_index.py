"""rebuild the global-env unique index as partial on SQLite

Revision 0055 created the ``uq_environment_is_global`` index with only a
PostgreSQL WHERE clause. Alembic ignores dialect-specific WHERE kwargs for
other dialects, so SQLite received a *full* unique index on
(org_id, is_global) — which collides for every non-global environment and
made creating a second custom environment fail with:

    UNIQUE constraint failed: environments.org_id, environments.is_global

Rebuild the index as a partial one (WHERE is_global IS TRUE) on SQLite.
PostgreSQL already has the correct partial index from 0055, so it is a no-op.

Revision ID: 0097_sqlite_partial_env_index
Revises: 0096_license_subscriptions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0097_sqlite_partial_env_index"
down_revision: str | None = "0096_license_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    op.execute("DROP INDEX IF EXISTS uq_environment_is_global")
    op.create_index(
        "uq_environment_is_global",
        "environments",
        ["org_id", "is_global"],
        unique=True,
        sqlite_where=sa.text("is_global IS TRUE"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    op.execute("DROP INDEX IF EXISTS uq_environment_is_global")
    op.create_index(
        "uq_environment_is_global",
        "environments",
        ["org_id", "is_global"],
        unique=True,
    )
