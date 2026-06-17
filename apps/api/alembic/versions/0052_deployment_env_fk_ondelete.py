"""DB-1: deployments.environment_id FK → ON DELETE SET NULL

0010_deployments created ``deployments.environment_id`` with a plain
``ForeignKey("environments.id")`` (NO ACTION), and the FK-alignment pass
(0039) listed every other deployments/workflows FK but missed this one. As a
result, deleting an environment still referenced by a deployment fails with a
raw FK-violation on Postgres instead of nulling the reference (the runner
treats a NULL env_id as the global/default env, exactly like
workflows.environment_id which is already SET NULL).

Postgres-only, same rationale as 0039: SQLite can't ALTER a constraint without
a table rebuild, doesn't enforce FK ondelete by default, and the test schema is
built via create_all (which picks up the model's new ondelete directly). The
authoritative drift check runs on Postgres via ``alembic check`` in CI.

The constraint name is Postgres' default for the inline FK created in 0010
(``<table>_<column>_fkey``).

Revision ID: 0052_deployment_env_fk_ondelete
Revises: 0051_license_key
Create Date: 2026-06-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0052_deployment_env_fk_ondelete"
down_revision: str | None = "0051_license_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "deployments_environment_id_fkey"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return  # see module docstring — FK ondelete is Postgres-only here
    op.drop_constraint(_CONSTRAINT, "deployments", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT,
        "deployments",
        "environments",
        ["environment_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.drop_constraint(_CONSTRAINT, "deployments", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT,
        "deployments",
        "environments",
        ["environment_id"],
        ["id"],
    )
