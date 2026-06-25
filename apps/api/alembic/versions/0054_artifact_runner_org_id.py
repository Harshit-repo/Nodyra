"""add org_id to artifacts and runners (MT gap B/F)

Revision ID: 0054_artifact_runner_org_id
Revises: 0053_user_sessions_valid_after
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0054_artifact_runner_org_id"
down_revision: str | None = "0053_user_sessions_valid_after"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(
            sa.Column(
                "org_id",
                sa.String(32),
                sa.ForeignKey(
                    "organizations.id",
                    name="fk_artifacts_org_id_organizations",
                    ondelete="CASCADE",
                ),
                nullable=False,
                server_default="default",
            )
        )
    with op.batch_alter_table("runners") as batch:
        batch.add_column(
            sa.Column(
                "org_id",
                sa.String(32),
                sa.ForeignKey(
                    "organizations.id",
                    name="fk_runners_org_id_organizations",
                    ondelete="CASCADE",
                ),
                nullable=False,
                server_default="default",
            )
        )
    op.create_index("ix_artifacts_org_id", "artifacts", ["org_id"])
    op.create_index("ix_runners_org_id", "runners", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_runners_org_id", table_name="runners")
    op.drop_index("ix_artifacts_org_id", table_name="artifacts")
    with op.batch_alter_table("runners") as batch:
        batch.drop_column("org_id")
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("org_id")
