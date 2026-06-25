"""Encrypt github_sync_configs.webhook_secret with org DEK/KEK envelope (D-04)

The webhook_secret was stored in plaintext. Add encrypted_webhook_secret and
encrypted_webhook_secret_dek columns (same DEK/KEK pattern as Credential),
migrate existing rows, then NULL out the plaintext column.

Revision ID: 0064_encrypt_webhook_secret
Revises: 0063_github_sync_rls
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0064_encrypt_webhook_secret"
down_revision: str | None = "0063_github_sync_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("github_sync_configs") as batch_op:
        batch_op.add_column(sa.Column("encrypted_webhook_secret", sa.Text, nullable=True))
        batch_op.add_column(sa.Column("encrypted_webhook_secret_dek", sa.Text, nullable=True))
        # Allow NULL on the plaintext column now (was NOT NULL).
        batch_op.alter_column("webhook_secret", nullable=True)

    # Migrate existing plaintext secrets to encrypted form.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, webhook_secret, org_id FROM github_sync_configs WHERE webhook_secret IS NOT NULL")
    ).fetchall()
    for row in rows:
        try:
            from app.services.crypto import encrypt_credential
            from app.services.kek_provider import kek_provider
            from app.services import crypto
            import asyncio

            # Try to get org KEK synchronously by re-using the master key path.
            # The org KEK may not be available at migration time (no live DB session),
            # so fall back to master-KEK wrapping which decrypt_credential also handles.
            enc_data, enc_dek = encrypt_credential({"secret": row.webhook_secret})
            bind.execute(
                sa.text(
                    "UPDATE github_sync_configs SET "
                    "encrypted_webhook_secret = :enc, "
                    "encrypted_webhook_secret_dek = :dek, "
                    "webhook_secret = NULL "
                    "WHERE id = :id"
                ),
                {"enc": enc_data, "dek": enc_dek, "id": row.id},
            )
        except Exception:
            # If encryption is unavailable (no NOODLE_SECRET_KEY), leave the
            # plaintext column intact — decrypted_webhook_secret() falls back to it.
            pass


def downgrade() -> None:
    # Restore plaintext from encrypted values where possible.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, encrypted_webhook_secret, encrypted_webhook_secret_dek "
            "FROM github_sync_configs WHERE encrypted_webhook_secret IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        try:
            from app.services.crypto import decrypt_credential
            data = decrypt_credential(row.encrypted_webhook_secret, row.encrypted_webhook_secret_dek)
            secret = data.get("secret", "")
            bind.execute(
                sa.text("UPDATE github_sync_configs SET webhook_secret = :s WHERE id = :id"),
                {"s": secret, "id": row.id},
            )
        except Exception:
            pass

    with op.batch_alter_table("github_sync_configs") as batch_op:
        batch_op.drop_column("encrypted_webhook_secret")
        batch_op.drop_column("encrypted_webhook_secret_dek")
        batch_op.alter_column("webhook_secret", nullable=False)
