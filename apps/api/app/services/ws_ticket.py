"""One-time WebSocket authentication tickets.

A browser can't send a custom ``Authorization`` header on a WebSocket
upgrade (the upgrade is issued by the browser's WS constructor, not
fetch).  Putting a session token in the URL query string leaks it into
server access logs and browser history.

Solution: before opening the socket the SPA calls ``POST /auth/ws-ticket``
which mints a short-lived, single-use ticket.  The ticket is stored in
Redis (or an in-process dict when Redis is unavailable) and deleted on
first use.  The WS endpoint then accepts ``?ticket=<token>`` instead of
``?token=<token>`` and rejects any ticket that has already been consumed.
"""

import secrets
from datetime import UTC, datetime

from app.config import settings

# ---------------------------------------------------------------------------
# In-process fallback store
# ---------------------------------------------------------------------------

_in_process: dict[str, tuple[str, datetime]] = {}  # ticket → (user_id, expires_at)


def _now() -> datetime:
    return datetime.now(UTC)


async def _redis_client():
    """Return the shared Redis client, or None if Redis is not configured."""
    try:
        from app.services.events import broker

        if broker._redis is not None:
            return broker._redis
    except Exception:
        pass
    # Try to build a standalone client if a redis_url is configured.
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis

            return aioredis.from_url(settings.redis_url, decode_responses=True)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_TICKET_PREFIX = "noodle:ws_ticket:"


async def create_ticket(user_id: str) -> str:
    """Mint a single-use ticket for ``user_id``.

    The ticket is a 32-byte URL-safe random token.  It expires after
    ``settings.ws_ticket_ttl_seconds`` seconds.
    """
    ticket = secrets.token_urlsafe(32)
    ttl = settings.ws_ticket_ttl_seconds

    redis = await _redis_client()
    if redis is not None:
        try:
            key = f"{_TICKET_PREFIX}{ticket}"
            await redis.set(key, user_id, ex=ttl)
            return ticket
        except Exception:
            pass

    # In-process fallback.
    from datetime import timedelta

    expires = _now() + timedelta(seconds=ttl)
    _in_process[ticket] = (user_id, expires)
    _evict_expired()
    return ticket


async def consume_ticket(ticket: str) -> str | None:
    """Consume a ticket, returning the associated ``user_id`` or ``None``.

    A ticket is valid only once — it is deleted immediately on first use so
    replay attacks are impossible even within the TTL window.
    """
    redis = await _redis_client()
    if redis is not None:
        try:
            key = f"{_TICKET_PREFIX}{ticket}"
            # Atomic GET + DEL: consume in a pipeline so two concurrent WS
            # upgrades can't both see the same ticket as valid.
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.get(key)
                await pipe.delete(key)
                results = await pipe.execute()
            user_id = results[0]
            return user_id if user_id else None
        except Exception:
            pass

    # In-process fallback.
    entry = _in_process.pop(ticket, None)
    if entry is None:
        return None
    user_id, expires = entry
    if _now() > expires:
        return None
    return user_id


def _evict_expired() -> None:
    now = _now()
    stale = [t for t, (_, exp) in _in_process.items() if now > exp]
    for t in stale:
        _in_process.pop(t, None)
