"""Org KEK resolution + credential envelope helpers (Phase E).

Every organization holds ``wrapped_org_kek`` — its KEK wrapped by the master
KEK (derived from SECRET_KEY today; the ``KekProvider`` seam is where a
Vault/KMS backend plugs in for the Enterprise ``external_secrets`` feature).
KEKs are minted lazily on first use with a compare-and-set so two replicas
can't each mint one and orphan the loser's credentials.
"""

from __future__ import annotations

import logging
from typing import Protocol

from cryptography.fernet import InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential, Organization
from app.services import crypto

_logger = logging.getLogger(__name__)


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

# Unwrapped KEKs cached per org.  Each entry carries a TTL so a KEK rotation
# on another replica is picked up within _KEK_CACHE_TTL_SECONDS without needing
# an explicit cross-process invalidation signal.
_KEK_CACHE_TTL_SECONDS = 300.0  # 5 minutes
_kek_cache: dict[str, tuple[bytes, float]] = {}


def invalidate_kek_cache(org_id: str | None = None) -> None:
    """Clear cached KEKs.  When ``org_id`` is None the entire cache is cleared."""
    if org_id is None:
        _kek_cache.clear()
    else:
        _kek_cache.pop(org_id, None)


async def get_org_kek(
    org_id: str | None, session: AsyncSession
) -> bytes | None:
    """The org's KEK, minting and persisting one on first use.

    ``None`` for a falsy/unknown org — callers fall back to the master-KEK
    envelope, which keeps pre-tenancy rows and exotic paths decryptable.
    """
    if not org_id:
        return None
    cached, cached_at = _kek_cache.get(org_id, (None, 0.0))
    if cached is not None and time.monotonic() - cached_at < _KEK_CACHE_TTL_SECONDS:
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
    try:
        kek = kek_provider.unwrap(org.wrapped_org_kek)
    except (InvalidToken, ValueError):
        _logger.error(
            "get_org_kek: cannot unwrap org KEK for org %s — SECRET_KEY may have "
            "changed since the KEK was minted. Falling back to master-KEK path. "
            "To recover: restore the original NOODLE_SECRET_KEY, OR run "
            "`UPDATE organizations SET wrapped_org_kek = NULL` in Postgres "
            "and delete any credentials that were encrypted under the old key.",
            org_id,
        )
        return None
    _kek_cache[org_id] = kek
    return kek


async def batch_get_org_keks(
    org_ids: list[str | None], session: AsyncSession
) -> dict[str | None, bytes | None]:
    """B-11: Batch-fetch KEKs for multiple orgs in a single query.

    Returns a dict keyed by org_id (including None). Unlike get_org_kek(),
    this path skips KEK minting — orgs without a wrapped_org_kek return None.
    Minting happens on the first write-path access via get_org_kek(). Cache
    hits are served from the process-level KEK cache with no DB round-trip.
    """
    result: dict[str | None, bytes | None] = {}
    uncached: list[str] = []
    for oid in org_ids:
        if not oid:
            result[oid] = None
            continue
        cached = _kek_cache.get(oid)
        if cached is not None:
            result[oid] = cached
        else:
            uncached.append(oid)

    if uncached:
        orgs = (
            await session.execute(
                select(Organization).where(Organization.id.in_(uncached))
            )
        ).scalars().all()
        by_id = {o.id: o for o in orgs}
        for oid in uncached:
            org = by_id.get(oid)
            if org is None or not org.wrapped_org_kek:
                result[oid] = None
                continue
            try:
                kek = kek_provider.unwrap(org.wrapped_org_kek)
            except (InvalidToken, ValueError):
                _logger.error(
                    "batch_get_org_keks: cannot unwrap KEK for org %s — SECRET_KEY may have changed",
                    oid,
                )
                result[oid] = None
                continue
            _kek_cache[oid] = kek
            result[oid] = kek

    return result


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
    credential: Credential, session: AsyncSession, *, strict: bool = False
) -> dict:
    """Decrypt a credential row via its org's KEK with legacy fallbacks.

    ``strict`` (H1) raises :class:`crypto.CredentialDecryptError` rather than
    returning ``{}`` when a credential that holds ciphertext cannot be
    decrypted — used on the execution path so a run fails loudly instead of
    silently dropping the credential.
    """
    org_kek = await get_org_kek(getattr(credential, "org_id", None), session)
    return crypto.decrypt_credential(
        credential.encrypted_data,
        credential.encrypted_dek,
        org_kek=org_kek,
        strict=strict,
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
