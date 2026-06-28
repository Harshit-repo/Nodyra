"""Secret redaction for logs, previews, events, and persisted run data."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential
from app.services.org_keys import decrypt_credential_for

REDACTED = "***REDACTED***"

# Per-org cache of decrypted credential values for substring redaction.
# In multi-tenant mode the cache is keyed by org_id so a worker processing
# a run for org-A never holds org-B's plaintext secrets in memory.
#
# Each entry is a (values, loaded_at) tuple.  The all-orgs fallback
# ``load_secret_values`` is kept for callers that lack org context
# (e.g. artifact downloads keyed solely by run_id), but new code should
# prefer ``load_secret_values_for_org``.
#
# Mutation endpoints (credentials create/update/delete) call
# ``invalidate_secret_cache()`` to drop the affected entry locally.
#
# Multi-replica safety net: ``SECRET_CACHE_TTL_SECONDS`` bounds how long a
# rotated credential stays in another replica's cache before it's
# re-decrypted from the DB. 60s caps the worst-case window where a freshly
# rotated value could still slip through redaction on a sibling replica.
SECRET_CACHE_TTL_SECONDS = 60.0
_secret_cache: dict[str, tuple[list[str], float]] = {}
_SECRET_CACHE_GLOBAL_KEY = "__all__"
_secret_cache_lock = asyncio.Lock()


def invalidate_secret_cache(org_id: str | None = None) -> None:
    """Drop cached secret values so the next call re-decrypts.

    When ``org_id`` is None the entire cache (all orgs) is cleared.
    Mutation endpoints pass the affected org so only one tenant's cache
    is invalidated while other tenants stay warm.
    """
    global _secret_cache
    if org_id is None:
        _secret_cache.clear()
    else:
        _secret_cache.pop(org_id, None)
        _secret_cache.pop(_SECRET_CACHE_GLOBAL_KEY, None)

SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "client_secret",
    "connection_url",
    "password",
    "private_key",
    "secret",
    "token",
)


def _is_sensitive_key(key: object) -> bool:
    text = str(key).lower()
    return any(part in text for part in SENSITIVE_KEY_PARTS)


def _usable_secret(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if len(stripped) < 4:
        return None
    return stripped


async def _decrypt_credential_values(
    session: AsyncSession, org_id: str | None = None
) -> list[str]:
    """Decrypt credential values, optionally scoped to one org.

    When ``org_id`` is None all credentials across all orgs are loaded
    (backward-compatible fallback).  Prefer ``load_secret_values_for_org``.
    """
    stmt = select(Credential)
    if org_id is not None:
        stmt = stmt.where(Credential.org_id == org_id)
    result = await session.scalars(stmt)
    values: list[str] = []
    for credential in result.all():
        data = await decrypt_credential_for(credential, session)
        for value in data.values():
            secret = _usable_secret(value)
            if secret is not None:
                values.append(secret)
    return sorted(set(values), key=len, reverse=True)


async def load_secret_values(session: AsyncSession) -> list[str]:
    """Load ALL known credential values (every org) for exact-match redaction.

    **Prefer ``load_secret_values_for_org`` in multi-tenant deployments.**
    This function is kept for callers that lack org context (e.g. artifact
    downloads keyed solely by run_id).

    Returns a process-cached list. Mutation endpoints invalidate the cache so
    new/changed/deleted credentials are picked up on the next call.
    """
    global _secret_cache
    now = time.monotonic()
    key = _SECRET_CACHE_GLOBAL_KEY
    entry = _secret_cache.get(key)
    if entry is not None and now - entry[1] < SECRET_CACHE_TTL_SECONDS:
        return entry[0]
    async with _secret_cache_lock:
        entry = _secret_cache.get(key)
        if entry is not None and now - entry[1] < SECRET_CACHE_TTL_SECONDS:
            return entry[0]
        values = await _decrypt_credential_values(session)
        _secret_cache[key] = (values, time.monotonic())
        return values


async def load_secret_values_for_org(
    org_id: str, session: AsyncSession
) -> list[str]:
    """Load credential values for a single org for exact-match redaction.

    In multi-tenant deployments this bounds the in-memory plaintext to the
    org being served by the current request/run.  Cache is per-org with the
    same TTL semantics as ``load_secret_values``.
    """
    global _secret_cache
    now = time.monotonic()
    entry = _secret_cache.get(org_id)
    if entry is not None and now - entry[1] < SECRET_CACHE_TTL_SECONDS:
        return entry[0]
    async with _secret_cache_lock:
        entry = _secret_cache.get(org_id)
        if entry is not None and now - entry[1] < SECRET_CACHE_TTL_SECONDS:
            return entry[0]
        values = await _decrypt_credential_values(session, org_id=org_id)
        _secret_cache[org_id] = (values, time.monotonic())
        return values


def redact_text(text: str, secret_values: Iterable[str] = ()) -> str:
    redacted = text
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    return redacted


def redact_value(value: Any, secret_values: Iterable[str] = ()) -> Any:
    """Return a JSON-compatible structure with secret-looking values masked."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return redact_text(value, secret_values)
    if isinstance(value, list | tuple):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, dict):
        next_value: dict[str, Any] = {}
        for key, item in value.items():
            out_key = str(key)
            if _is_sensitive_key(key):
                next_value[out_key] = REDACTED if item not in (None, "") else item
            else:
                next_value[out_key] = redact_value(item, secret_values)
        return next_value
    return redact_text(str(value), secret_values)
