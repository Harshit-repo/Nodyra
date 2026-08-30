"""Edition/feature resolution from a signed offline license key.

Resolution order: ``settings.license_key`` (env) → ``system_settings.license_key``
(DB) → Community. Verified locally against an Ed25519 public key
(``settings.license_public_key`` or the baked default). Cached on a short TTL
like ``live_settings``/``org_limits``. ``0`` limit = unlimited (matches the
org_limits convention).

``SessionLocal`` is imported at module level so the test harness can patch
``licensing.SessionLocal`` onto the per-test session (see tests/conftest.py).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, replace
from enum import StrEnum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException
from fastapi import status as _http_status
from sqlalchemy import func, select

from app.config import settings as boot_settings
from app.db import SessionLocal

logger = logging.getLogger(__name__)

# Production public key. Verifies license keys signed offline with the matching
# PRIVATE key (kept out of the repo — see docs/licensing-internal.md and the
# .secrets/ directory). ``settings.license_public_key`` overrides this when set
# (tests/staging use their own throwaway keypair).
_BAKED_PUBLIC_KEY_PEM = """\
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAGdBDiijpMRnL4/lki8urg/wGX3Iw8nZuMKLqNZt+/6U=
-----END PUBLIC KEY-----
"""

_CACHE_TTL_SECONDS = 30.0


class Edition(StrEnum):
    COMMUNITY = "community"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Feature(StrEnum):
    SANDBOX = "sandbox"
    OBSERVABILITY = "observability"
    GIT_SYNC = "git_sync"
    MULTI_TENANCY = "multi_tenancy"
    SSO = "sso"
    EXTERNAL_KMS = "external_kms"
    AUDIT_LOGS = "audit_logs"
    ADVANCED_RBAC = "advanced_rbac"
    DEDICATED_POOLS = "dedicated_pools"


@dataclass(frozen=True)
class ResourceLimits:
    environments: int
    runners: int
    deployments: int
    seats: int


@dataclass(frozen=True)
class License:
    edition: Edition
    features: frozenset
    limits: ResourceLimits
    customer: str | None
    expires_at: int | None
    valid: bool
    notice: str | None


_COMMUNITY_FEATURES = frozenset({Feature.SANDBOX})
_PRO_FEATURES = _COMMUNITY_FEATURES | frozenset({Feature.OBSERVABILITY, Feature.GIT_SYNC})
_ENT_FEATURES = _PRO_FEATURES | frozenset(
    {
        Feature.MULTI_TENANCY,
        Feature.SSO,
        Feature.EXTERNAL_KMS,
        Feature.AUDIT_LOGS,
        Feature.ADVANCED_RBAC,
        Feature.DEDICATED_POOLS,
    }
)

# (features, ResourceLimits) per tier. 0 = unlimited.
TIER_DEFAULTS: dict[Edition, tuple[frozenset, ResourceLimits]] = {
    Edition.COMMUNITY: (
        _COMMUNITY_FEATURES,
        ResourceLimits(environments=3, runners=1, deployments=10, seats=5),
    ),
    Edition.PRO: (
        _PRO_FEATURES,
        ResourceLimits(environments=10, runners=5, deployments=0, seats=10),
    ),
    Edition.ENTERPRISE: (
        _ENT_FEATURES,
        ResourceLimits(environments=0, runners=0, deployments=0, seats=0),
    ),
}

_COMMUNITY = License(
    edition=Edition.COMMUNITY,
    features=TIER_DEFAULTS[Edition.COMMUNITY][0],
    limits=TIER_DEFAULTS[Edition.COMMUNITY][1],
    customer=None,
    expires_at=None,
    valid=True,
    notice=None,
)

_cache: tuple[float, License] | None = None


def invalidate_license_cache() -> None:
    global _cache
    _cache = None


def _public_key() -> Ed25519PublicKey | None:
    pem = boot_settings.license_public_key or _BAKED_PUBLIC_KEY_PEM
    if not pem:
        return None
    try:
        key = serialization.load_pem_public_key(pem.encode())
    except ValueError:
        return None
    return key if isinstance(key, Ed25519PublicKey) else None


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify(raw: str) -> dict | None:
    """Return the payload dict if the signature is valid, else None."""
    key = _public_key()
    if key is None or "." not in raw:
        return None
    body, sig = raw.rsplit(".", 1)
    try:
        key.verify(_b64url_decode(sig), body.encode())
        return json.loads(_b64url_decode(body))
    except (InvalidSignature, ValueError, json.JSONDecodeError):
        return None


def _community(notice: str | None) -> License:
    return replace(_COMMUNITY, notice=notice) if notice else _COMMUNITY


def _build(payload: dict) -> License:
    try:
        edition = Edition(payload.get("tier", "community"))
    except ValueError:
        return _community("Unknown license tier; treating as Community.")
    exp = payload.get("expires_at")
    if exp is not None and time.time() > exp:
        return _community("License expired; reverted to Community.")
    base_features, base_limits = TIER_DEFAULTS[edition]
    extra = set()
    for name in payload.get("features", []) or []:
        try:
            extra.add(Feature(name))
        except ValueError:
            pass
    overrides = payload.get("limits", {}) or {}
    limits = ResourceLimits(
        environments=overrides.get("environments", base_limits.environments),
        runners=overrides.get("runners", base_limits.runners),
        deployments=overrides.get("deployments", base_limits.deployments),
        seats=overrides.get("seats", base_limits.seats),
    )
    return License(
        edition=edition,
        features=frozenset(base_features) | frozenset(extra),
        limits=limits,
        customer=payload.get("customer"),
        expires_at=exp,
        valid=True,
        notice=None,
    )


async def _db_license_key() -> str | None:
    from app.models import SystemSetting

    try:
        async with SessionLocal() as session:
            row = await session.get(SystemSetting, "singleton")
            return getattr(row, "license_key", None) if row is not None else None
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        # This is a lookup, not a gate: "we could not reach the database"
        # and "no key is stored" have the same correct answer, Community.
        # The narrower tuple (OSError, OperationalError, ProgrammingError)
        # missed driver errors raised while the connection is still being
        # established, before SQLAlchemy classifies them — asyncpg's
        # InvalidCatalogNameError for a database that does not exist yet
        # being the ordinary case on a fresh deployment. Letting that
        # escape turns every entitlement check into a 500.
        logger.debug("license key lookup failed; assuming none", exc_info=True)
        return None


async def current_license() -> License:
    global _cache
    now = time.monotonic()
    if _cache is not None and now < _cache[0]:
        return _cache[1]
    raw = boot_settings.license_key or (await _db_license_key()) or ""
    if not raw:
        lic = _COMMUNITY
    else:
        payload = _verify(raw)
        lic = (
            _build(payload)
            if payload is not None
            else _community("License key signature invalid; treating as Community.")
        )
    _cache = (now + _CACHE_TTL_SECONDS, lic)
    return lic


async def has_feature(feature: Feature) -> bool:
    return feature in (await current_license()).features


async def resource_limit(name: str) -> int:
    return getattr((await current_license()).limits, name)


class LicenseLimitError(HTTPException):
    def __init__(self, kind: str, current: int, limit: int, edition: Edition):
        super().__init__(
            status_code=_http_status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "license_limit",
                "resource": kind,
                "current": current,
                "limit": limit,
                "edition": edition.value,
                "message": (
                    f"{kind}: {current}/{limit} on the {edition.value} edition. "
                    "Upgrade to raise this limit."
                ),
            },
        )


async def enforce_resource_cap(session, kind: str) -> None:
    """Raise ``LicenseLimitError`` if creating one more ``kind`` would exceed the
    licensed cap. ``0`` = unlimited. Counts are org-filtered automatically by the
    ORM hook (a no-op while single-tenant)."""
    limit = await resource_limit(kind)
    if limit == 0:
        return
    from app.models import Deployment, Environment, RunnerPool, User

    model = {
        "environments": Environment,
        "runners": RunnerPool,
        "deployments": Deployment,
        "seats": User,
    }[kind]
    stmt = select(func.count()).select_from(model)
    if kind == "deployments":
        stmt = stmt.where(model.active.is_(True))
    current = await session.scalar(stmt) or 0
    if current >= limit:
        lic = await current_license()
        raise LicenseLimitError(kind, current, limit, lic.edition)


def require_feature(feature: Feature):
    """FastAPI dependency: 402 unless the active license grants ``feature``."""

    async def _dep() -> None:
        if not await has_feature(feature):
            lic = await current_license()
            raise HTTPException(
                status_code=_http_status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "feature_locked",
                    "feature": feature.value,
                    "edition": lic.edition.value,
                    "message": (
                        f"'{feature.value}' requires a higher edition "
                        f"(current: {lic.edition.value})."
                    ),
                },
            )

    return _dep


async def reconcile_capabilities() -> list[str]:
    """At startup, force config-requested capabilities the license does NOT grant
    back to their disabled state and return human-readable warnings. Never raises —
    self-hosted operators must still boot."""
    warnings: list[str] = []
    if boot_settings.multi_tenancy_enabled and not await has_feature(Feature.MULTI_TENANCY):
        boot_settings.multi_tenancy_enabled = False
        warnings.append(
            "multi_tenancy_enabled requires the Enterprise edition; "
            "disabled (running single-tenant)."
        )
    if boot_settings.otel_enabled and not await has_feature(Feature.OBSERVABILITY):
        boot_settings.otel_enabled = False
        warnings.append("otel_enabled requires the Pro edition or higher; disabled.")
    if boot_settings.kms_provider != "env" and not await has_feature(Feature.EXTERNAL_KMS):
        _kms_provider = boot_settings.kms_provider
        boot_settings.kms_provider = "env"
        warnings.append(
            f"kms_provider={_kms_provider!r} requires the Enterprise edition; "
            "reverted to 'env'."
        )
    return warnings


__all__ = [
    "Edition",
    "Feature",
    "ResourceLimits",
    "License",
    "TIER_DEFAULTS",
    "current_license",
    "has_feature",
    "resource_limit",
    "invalidate_license_cache",
    "LicenseLimitError",
    "enforce_resource_cap",
    "require_feature",
    "reconcile_capabilities",
]
