"""Runner SSH onboarding columns

Revision ID: 0019_runner_ssh
Revises: 0018_remote_runners
Create Date: 2026-05-28

Adds nullable columns to ``runners`` so a machine onboarded over SSH can store
its (encrypted) SSH credentials for later restart / re-provision:
  * ssh_host        — "user@host:port" for display
  * ssh_credentials — Fernet-encrypted JSON blob (host/port/user/secret)
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0019_runner_ssh"
down_revision: str | None = "0018_remote_runners"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "runners" not in set(inspect(op.get_bind()).get_table_names()):
        return
    cols = _columns("runners")
    with op.batch_alter_table("runners") as batch:
        if "ssh_host" not in cols:
            batch.add_column(sa.Column("ssh_host", sa.String(255), nullable=True))
        if "ssh_credentials" not in cols:
            batch.add_column(sa.Column("ssh_credentials", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runners") as batch:
        batch.drop_column("ssh_credentials")
        batch.drop_column("ssh_host")
