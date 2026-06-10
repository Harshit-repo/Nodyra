"""Org KEK resolution + credential envelope helpers (Phase E).

Every organization holds ``wrapped_org_kek`` — its KEK wrapped by the master
KEK (derived from SECRET_KEY today; the ``KekProvider`` seam is where a
Vault/KMS backend plugs in for the Enterprise ``external_secrets`` feature).
KEKs are minted lazily on first use with a compare-and-set so two replicas
can't each mint one and orphan the loser's credentials.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential, Organization
from app.services import crypto


class KekProvider(Protocol):
    """Wraps/unwraps org KEKs with the deployment's root of trust."""

    def wrap(self, org_kek: bytes) -> str: ...
    def unwrap(self, wrapped: str) -> bytes: ...


class EnvMasterKekProvider:
    """Default provider: master Fernet key derived from SECRET_KEY."""

    @staticmethod
    def wrap(org_kek: bytes) -> str:
        return crypto.wrap_org_kek(org_kek)

    @staticmethod
    def unwrap(wrapped: str) -> bytes:
        return crypto.unwrap_org_kek(wrapped)


# Swapped at startup for KMS/Vault deployments (Enterprise external_secrets).
kek_provider: KekProvider = EnvMasterKekProvider()

# Unwrapped KEKs cached per org for the process lifetime. Rotation calls
# invalidate_kek_cache(); regular use never changes a KEK in place.
_kek_cache: dict[str, bytes] = {}


def invalidate_kek_cache() -> None:
    _kek_cache.clear()


async def get_org_kek(
    org_id: str | None, session: AsyncSession
) -> bytes | None:
    """The org's KEK, minting and persisting one on first use.

    ``None`` for a falsy/unknown org — callers fall back to the master-KEK
    envelope, which keeps pre-tenancy rows and exotic paths decryptable.
    """
    if not org_id:
        return None
    cached = _kek_cache.get(org_id)
    if cached is not None:
        return cached
    org = await session.get(Organization, org_id)
    if org is None:
        return None
    if not org.wrapped_org_kek:
        # Compare-and-set mint: only the writer that finds the column still
        # NULL wins; everyone re-reads the winning value afterwards, so two
        # replicas can never split an org across two KEKs.
        await session.execute(
            update(Organization)
            .where(
                Organization.id == org_id,
                Organization.wrapped_org_kek.is_(None),
            )
            .values(wrapped_org_kek=kek_provider.wrap(crypto.generate_org_kek()))
        )
        await session.flush()
        await session.refresh(org)
    kek = kek_provider.unwrap(org.wrapped_org_kek)
    _kek_cache[org_id] = kek
    return kek


async def encrypt_credential_for(
    org_id: str | None, data: dict, session: AsyncSession
) -> tuple[str, str]:
    """Encrypt credential data under the org's envelope (master if no org)."""
    return crypto.encrypt_credential(data, org_kek=await get_org_kek(org_id, session))


async def encrypt_credential_current(
    data: dict, session: AsyncSession
) -> tuple[str, str]:
    """Encrypt for the request's org — the org a new Credential row will be
    stamped with at flush (see tenancy.stamp)."""
    from app.tenancy import DEFAULT_ORG_ID, active_org_id

    return await encrypt_credential_for(
        active_org_id() or DEFAULT_ORG_ID, data, session
    )


async def decrypt_credential_for(
    credential: Credential, session: AsyncSession
) -> dict:
    """Decrypt a credential row via its org's KEK with legacy fallbacks."""
    org_kek = await get_org_kek(getattr(credential, "org_id", None), session)
    return crypto.decrypt_credential(
        credential.encrypted_data, credential.encrypted_dek, org_kek=org_kek
    )


async def rewrap_org_credentials(session: AsyncSession, org_id: str) -> int:
    """Rewrap an org's master-wrapped DEKs under its org KEK.

    Used by the Phase E migration (no-op risk: pre-E data is single-org) and
    by future per-org rotation. KEK-direct legacy rows (NULL dek) are left
    for the decrypt fallback; already-org-wrapped DEKs are skipped.
    Returns the number of credentials rewrapped.
    """
    org_kek = await get_org_kek(org_id, session)
    if org_kek is None:
        return 0
    rewrapped = 0
    result = await session.scalars(
        Credential.__table__.select().with_only_columns(Credential.id)
        .where(Credential.org_id == org_id, Credential.encrypted_dek.is_not(None))
    )
    for cred_id in result.all():
        cred = await session.get(Credential, cred_id)
        try:
            cred.encrypted_dek = crypto.rewrap_dek(cred.encrypted_dek, org_kek)
            rewrapped += 1
        except Exception:  # noqa: BLE001 - already org-wrapped or foreign; skip
            continue
    return rewrapped
