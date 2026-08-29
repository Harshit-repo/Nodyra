"""Subscription registry for self-serve licensing

Adds the vendor-side tables that turn manual key minting into a subscription:
who bought what, whether they are still entitled, and an idempotency ledger so a
retried Stripe webhook cannot mint a second licence for one payment.

Both tables live only on the instance the vendor runs as its licence server. A
customer's deployment creates them empty and never writes a row — its licence is
still verified entirely offline.

No RLS policies: neither table carries an ``org_id``. Subscriptions describe
whole customer instances, not organizations inside one, so the tenant boundary
does not apply. (``test_rls_coverage`` derives its expectations from models with
an ``org_id``, so these are correctly out of scope.)

Revision ID: 0096_license_subscriptions
Revises: 0095_schedule_occurrence_outbox
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0096_license_subscriptions"
down_revision: str | None = "0095_schedule_occurrence_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "license_subscriptions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="stripe"),
        sa.Column("provider_customer_id", sa.Text(), nullable=True),
        # Unique so a webhook retry cannot create a second row for one
        # subscription, and so the lookup in _apply_event is unambiguous.
        sa.Column("provider_subscription_id", sa.Text(), nullable=True),
        sa.Column("customer_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("customer_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("tier", sa.String(length=32), nullable=False, server_default="pro"),
        sa.Column("seats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "extra_features",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # SHA-256 digest only. The token itself is shown once, at issuance.
        sa.Column("refresh_token_hash", sa.Text(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_license_subscriptions_provider_customer_id",
        "license_subscriptions",
        ["provider_customer_id"],
    )
    op.create_index(
        "ix_license_subscriptions_provider_subscription_id",
        "license_subscriptions",
        ["provider_subscription_id"],
        unique=True,
    )
    op.create_index(
        "ix_license_subscriptions_customer_email",
        "license_subscriptions",
        ["customer_email"],
    )
    # The refresh endpoint looks a subscription up by this digest on every
    # renewal; without an index that is a table scan per call.
    op.create_index(
        "ix_license_subscriptions_refresh_token_hash",
        "license_subscriptions",
        ["refresh_token_hash"],
    )
    op.create_index(
        "ix_license_subscriptions_status", "license_subscriptions", ["status"]
    )
    # Serves the "which subscriptions lapse soon" sweep.
    op.create_index(
        "ix_license_subscriptions_status_expires",
        "license_subscriptions",
        ["status", "expires_at"],
    )

    op.create_table(
        "processed_webhook_events",
        # The provider's own event id is the primary key: that is exactly the
        # idempotency guarantee wanted, enforced by the database rather than by
        # a check-then-insert race in application code.
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="stripe"),
        sa.Column("event_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("processed_webhook_events")
    for index in (
        "ix_license_subscriptions_status_expires",
        "ix_license_subscriptions_status",
        "ix_license_subscriptions_refresh_token_hash",
        "ix_license_subscriptions_customer_email",
        "ix_license_subscriptions_provider_subscription_id",
        "ix_license_subscriptions_provider_customer_id",
    ):
        op.drop_index(index, table_name="license_subscriptions")
    op.drop_table("license_subscriptions")
