"""Add interpreter selection and runtime flags to environments.

Revision ID: 0088_environment_interpreter_flags
Revises: 0087_runs_error
"""

import sqlalchemy as sa
from alembic import op

revision = "0088_environment_interpreter_flags"
down_revision = "0087_runs_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic creates ``alembic_version.version_num`` as VARCHAR(32), but this
    # revision identifier is 34 characters. PostgreSQL enforces that width and
    # otherwise fails while recording the revision after this upgrade. SQLite
    # does not enforce VARCHAR widths and cannot alter the column in place.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(32),
            type_=sa.String(64),
            existing_nullable=False,
        )
    op.add_column(
        "environments",
        sa.Column("interpreter", sa.String(20), nullable=False, server_default="cpython"),
    )
    op.add_column(
        "environments",
        sa.Column("runtime_flags", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "environment_build_jobs",
        sa.Column("interpreter", sa.String(20), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("environment_build_jobs", "interpreter")
    op.drop_column("environments", "runtime_flags")
    op.drop_column("environments", "interpreter")
