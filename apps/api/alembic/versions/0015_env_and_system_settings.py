"""environment runner pool + description, and singleton system_settings

Revision ID: 0015_env_and_system_settings
Revises: 0014_user_profile_fields
Create Date: 2026-05-26

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0015_env_and_system_settings"
down_revision: str | None = "0014_user_profile_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _columns("environments")
    with op.batch_alter_table("environments") as batch:
        if "description" not in existing:
            batch.add_column(
                sa.Column(
                    "description", sa.Text(), nullable=False, server_default=""
                )
            )
        if "runner_pool_size" not in existing:
            batch.add_column(
                sa.Column(
                    "runner_pool_size",
                    sa.Integer(),
                    nullable=False,
                    server_default="1",
                )
            )

    if "system_settings" not in _tables():
        op.create_table(
            "system_settings",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "max_concurrent_runs",
                sa.Integer(),
                nullable=False,
                server_default="8",
            ),
            sa.Column(
                "runner_idle_seconds",
                sa.Integer(),
                nullable=False,
                server_default="600",
            ),
            sa.Column(
                "run_retention_days",
                sa.Integer(),
                nullable=False,
                server_default="14",
            ),
            sa.Column(
                "run_retention_max_per_workflow",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "max_output_bytes",
                sa.Integer(),
                nullable=False,
                server_default=str(256 * 1024),
            ),
            sa.Column(
                "max_artifact_bytes",
                sa.Integer(),
                nullable=False,
                server_default=str(50 * 1024 * 1024),
            ),
            sa.Column(
                "max_artifacts_per_run",
                sa.Integer(),
                nullable=False,
                server_default="100",
            ),
            sa.Column(
                "app_timezone",
                sa.String(64),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.execute(
            "INSERT INTO system_settings (id) VALUES ('singleton')"
        )


def downgrade() -> None:
    if "system_settings" in _tables():
        op.drop_table("system_settings")
    existing = _columns("environments")
    with op.batch_alter_table("environments") as batch:
        if "runner_pool_size" in existing:
            batch.drop_column("runner_pool_size")
        if "description" in existing:
            batch.drop_column("description")
