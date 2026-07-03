"""Phase E (multi-tenancy): mint default-org KEK + rewrap credential DEKs.

Runs while all data still belongs to the single "default" org — that is the
whole point of pulling Phase E forward: this is a trivial sweep now and a
risky, audited live re-encryption later. Only the DEK *wrapping* changes;
credential ciphertext is untouched.

KEK-direct legacy rows (``encrypted_dek IS NULL``) are left for the decrypt
fallback chain. Rows whose DEK doesn't unwrap with the master KEK (foreign
SECRET_KEY, corruption) are skipped — they were already undecryptable and the
fallback keeps treating them the same way.

D-10: this migration inlines the crypto helpers it needs instead of importing
``app.services.crypto``. Migration isolation principle: a migration must never
import application code — app modules evolve and can break replayed migrations.
All crypto here is pure ``cryptography`` library calls with no app dependencies.

Revision ID: 0043_org_kek
Revises: 0042_rls
Create Date: 2026-06-10
"""

import base64
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

revision: str = "0043_org_kek"
down_revision: str | None = "0042_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ORG_ID = "default"


def _master_fernet() -> Fernet:
    """Derive the master Fernet key from SECRET_KEY (matches crypto._fernet())."""
    import os as _os

    secret = (
        _os.environ.get("NODYRA_SECRET_KEY")
        or _os.environ.get("NOODLE_SECRET_KEY")
        or _os.environ.get("SECRET_KEY", "")
    )
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"nodyra-credential-kek",
    ).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(raw))


def _generate_org_kek() -> bytes:
    return Fernet.generate_key()


def _wrap_org_kek(org_kek: bytes) -> str:
    return _master_fernet().encrypt(org_kek).decode()


def _unwrap_org_kek(wrapped: str) -> bytes:
    return _master_fernet().decrypt(wrapped.encode())


def _rewrap_dek(encrypted_dek: str, org_kek: bytes) -> str:
    """Unwrap DEK from master KEK, re-wrap under org KEK."""
    dek = _master_fernet().decrypt(encrypted_dek.encode())
    return Fernet(org_kek).encrypt(dek).decode()


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT id, wrapped_org_kek FROM organizations WHERE id = :id"),
        {"id": DEFAULT_ORG_ID},
    ).fetchone()
    if row is None:
        return  # fresh DB without the seed org; nothing to rewrap
    if row[1]:
        org_kek = _unwrap_org_kek(row[1])
    else:
        org_kek = _generate_org_kek()
        bind.execute(
            sa.text(
                "UPDATE organizations SET wrapped_org_kek = :wrapped "
                "WHERE id = :id AND wrapped_org_kek IS NULL"
            ),
            {"wrapped": _wrap_org_kek(org_kek), "id": DEFAULT_ORG_ID},
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
            rewrapped = _rewrap_dek(encrypted_dek, org_kek)
        except Exception:  # noqa: BLE001 - not master-wrapped; leave as-is
            continue
        bind.execute(
            sa.text("UPDATE credentials SET encrypted_dek = :dek WHERE id = :id"),
            {"dek": rewrapped, "id": cred_id},
        )


def downgrade() -> None:
    # Reverse: rewrap org-wrapped DEKs back under the master KEK.
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT wrapped_org_kek FROM organizations WHERE id = :id"),
        {"id": DEFAULT_ORG_ID},
    ).fetchone()
    if row is None or not row[0]:
        return
    org_kek = _unwrap_org_kek(row[0])
    credentials = bind.execute(
        sa.text(
            "SELECT id, encrypted_dek FROM credentials "
            "WHERE org_id = :org AND encrypted_dek IS NOT NULL"
        ),
        {"org": DEFAULT_ORG_ID},
    ).fetchall()
    master = _master_fernet()
    for cred_id, encrypted_dek in credentials:
        try:
            dek = Fernet(org_kek).decrypt(encrypted_dek.encode())
        except (InvalidToken, ValueError):
            continue
        bind.execute(
            sa.text("UPDATE credentials SET encrypted_dek = :dek WHERE id = :id"),
            {"dek": master.encrypt(dek).decode(), "id": cred_id},
        )
    bind.execute(
        sa.text("UPDATE organizations SET wrapped_org_kek = NULL WHERE id = :id"),
        {"id": DEFAULT_ORG_ID},
    )
