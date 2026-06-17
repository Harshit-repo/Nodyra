"""Phase E (multi-tenancy): mint default-org KEK + rewrap credential DEKs.

Runs while all data still belongs to the single "default" org — that is the
whole point of pulling Phase E forward: this is a trivial sweep now and a
risky, audited live re-encryption later. Only the DEK *wrapping* changes;
credential ciphertext is untouched.

KEK-direct legacy rows (``encrypted_dek IS NULL``) are left for the decrypt
fallback chain. Rows whose DEK doesn't unwrap with the master KEK (foreign
SECRET_KEY, corruption) are skipped — they were already undecryptable and the
fallback keeps treating them the same way.

Revision ID: 0043_org_kek
Revises: 0042_rls
Create Date: 2026-06-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_org_kek"
down_revision: str | None = "0042_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ORG_ID = "default"


def upgrade() -> None:
    from app.services import crypto

    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT id, wrapped_org_kek FROM organizations WHERE id = :id"),
        {"id": DEFAULT_ORG_ID},
    ).fetchone()
    if row is None:
        return  # fresh DB without the seed org; nothing to rewrap
    if row[1]:
        org_kek = crypto.unwrap_org_kek(row[1])
    else:
        org_kek = crypto.generate_org_kek()
        bind.execute(
            sa.text(
                "UPDATE organizations SET wrapped_org_kek = :wrapped "
                "WHERE id = :id AND wrapped_org_kek IS NULL"
            ),
            {"wrapped": crypto.wrap_org_kek(org_kek), "id": DEFAULT_ORG_ID},
        )

    credentials = bind.execute(
        sa.text(
            "SELECT id, encrypted_dek FROM credentials "
            "WHERE org_id = :org AND encrypted_dek IS NOT NULL"
        ),
        {"org": DEFAULT_ORG_ID},
    ).fetchall()
    for cred_id, encrypted_dek in credentials:
        try:
            rewrapped = crypto.rewrap_dek(encrypted_dek, org_kek)
        except Exception:  # noqa: BLE001 - not master-wrapped; leave as-is
            continue
        bind.execute(
            sa.text("UPDATE credentials SET encrypted_dek = :dek WHERE id = :id"),
            {"dek": rewrapped, "id": cred_id},
        )


def downgrade() -> None:
    # Reverse: rewrap org-wrapped DEKs back under the master KEK.
    from cryptography.fernet import Fernet, InvalidToken

    from app.services import crypto

    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT wrapped_org_kek FROM organizations WHERE id = :id"),
        {"id": DEFAULT_ORG_ID},
    ).fetchone()
    if row is None or not row[0]:
        return
    org_kek = crypto.unwrap_org_kek(row[0])
    credentials = bind.execute(
        sa.text(
            "SELECT id, encrypted_dek FROM credentials "
            "WHERE org_id = :org AND encrypted_dek IS NOT NULL"
        ),
        {"org": DEFAULT_ORG_ID},
    ).fetchall()
    for cred_id, encrypted_dek in credentials:
        try:
            dek = Fernet(org_kek).decrypt(encrypted_dek.encode())
        except (InvalidToken, ValueError):
            continue
        bind.execute(
            sa.text("UPDATE credentials SET encrypted_dek = :dek WHERE id = :id"),
            {"dek": crypto.wrap_dek(dek), "id": cred_id},
        )
    bind.execute(
        sa.text(
            "UPDATE organizations SET wrapped_org_kek = NULL WHERE id = :id"
        ),
        {"id": DEFAULT_ORG_ID},
    )
