"""Secret redaction for logs, previews, events, and persisted run data."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Credential
from app.services.crypto import decrypt_credential

REDACTED = "***REDACTED***"

# Process-local cache of decrypted credential values for substring redaction.
# Decrypting every credential on every run start is otherwise O(N) Fernet ops
# per run; this trades a small memory cost for skipping the rebuild on the hot
# path. Mutation endpoints (credentials create/update/delete) call
# ``invalidate_secret_cache()`` to drop the cache locally.
#
# Multi-replica safety net: ``SECRET_CACHE_TTL_SECONDS`` bounds how long a
# rotated credential stays in another replica's cache before it's
# re-decrypted from the DB. 60s caps the worst-case window where a freshly
# rotated value could still slip through redaction on a sibling replica.
SECRET_CACHE_TTL_SECONDS = 60.0
_secret_cache: list[str] | None = None
_secret_cache_loaded_at: float = 0.0
_secret_cache_lock = asyncio.Lock()


def invalidate_secret_cache() -> None:
    """Drop the cached secret values so the next call re-decrypts."""
    global _secret_cache, _secret_cache_loaded_at
    _secret_cache = None
    _secret_cache_loaded_at = 0.0

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


async def load_secret_values(session: AsyncSession) -> list[str]:
    """Load known credential values for exact-match redaction.

    Returns a process-cached list. Mutation endpoints invalidate the cache so
    new/changed/deleted credentials are picked up on the next call. The
    cache also expires after ``SECRET_CACHE_TTL_SECONDS`` so a credential
    rotated on another replica isn't redacted with the stale value forever.
    """
    global _secret_cache, _secret_cache_loaded_at
    now = time.monotonic()
    if (
        _secret_cache is not None
        and now - _secret_cache_loaded_at < SECRET_CACHE_TTL_SECONDS
    ):
        return _secret_cache
    async with _secret_cache_lock:
        if (
            _secret_cache is not None
            and now - _secret_cache_loaded_at < SECRET_CACHE_TTL_SECONDS
        ):
            return _secret_cache
        result = await session.scalars(select(Credential))
        values: list[str] = []
        for credential in result.all():
            data = decrypt_credential(
                credential.encrypted_data, credential.encrypted_dek
            )
            for value in data.values():
                secret = _usable_secret(value)
                if secret is not None:
                    values.append(secret)
        # Longest first avoids partially redacting a prefix before the full value.
        _secret_cache = sorted(set(values), key=len, reverse=True)
        _secret_cache_loaded_at = time.monotonic()
        return _secret_cache


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
