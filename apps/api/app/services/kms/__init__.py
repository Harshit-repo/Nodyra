"""KMS provider factory.

``get_kms_provider()`` returns a singleton based on ``settings.kms_provider``.
The singleton is cached via ``functools.lru_cache`` (size=1) and invalidated
when tests call ``invalidate_kms_cache()``.
"""

from __future__ import annotations

from app.config import settings
from app.services.kms.base import KMSProvider

__all__ = [
    "KMSProvider",
    "get_kms_provider",
    "invalidate_kms_cache",
]


def _build() -> KMSProvider:
    provider = settings.kms_provider
    if provider == "env":
        from app.services.kms.env_kms import EnvKMSProvider

        return EnvKMSProvider()
    if provider == "vault":
        from app.services.kms.vault import VaultKMSProvider

        return VaultKMSProvider(
            vault_url=settings.vault_url,
            token=settings.vault_token,
            mount=settings.vault_transit_mount,
            key_name=settings.vault_transit_key,
        )
    if provider == "aws":
        from app.services.kms.aws_kms import AWSKMSProvider

        return AWSKMSProvider(
            key_id=settings.aws_kms_key_id,
            region=settings.aws_kms_region,
        )
    if provider == "gcp":
        from app.services.kms.gcp_kms import GCPKMSProvider

        return GCPKMSProvider(
            key_name=settings.gcp_kms_key_name,
        )
    msg = f"Unknown kms_provider: {provider!r}"
    raise ValueError(msg)


# Cache of exactly one — LRU size-1 so tests can invalidate with .cache_clear().
# Not lru_cache on _build directly because Settings is mutable at startup.
import functools  # noqa: E402
get_kms_provider = functools.lru_cache(maxsize=1)(_build)
"""Return the singleton KMS provider for the current ``settings.kms_provider``.

The singleton is created lazily on first access and cached forever (or until
``invalidate_kms_cache()`` is called).
"""


def invalidate_kms_cache() -> None:
    """Clear the cached KMS provider singleton.

    Used by tests to force re-creation with different settings between cases.
    """
    get_kms_provider.cache_clear()
