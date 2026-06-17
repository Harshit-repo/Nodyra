"""Shared sliding-window rate limiter (H5).

A single primitive used by both ``/auth`` brute-force friction and public
``/webhook`` ingress. When the queue backend is Redis the counter is shared
across replicas via ``INCR`` + ``EXPIRE`` (fixed window); otherwise — or when
Redis is unreachable — it degrades to an in-process sliding-window deque, which
is correct for single-process / self-hosted deployments.

``allow()`` returns ``True`` when the hit is within budget and ``False`` when it
should be rejected. Callers translate ``False`` into a 429.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from time import monotonic

from app.config import settings

logger = logging.getLogger(__name__)

# identifier-keyed sliding windows of hit timestamps (monotonic seconds).
# Each deque is bounded to ``limit`` entries — once full the oldest entry is
# popped before appending a new one, so no key can grow beyond the window's
# capacity regardless of request volume.
_buckets: dict[str, deque[float]] = defaultdict(deque)

# Sweep stale keys once the dict exceeds this watermark. Tied to the number
# of distinct identifiers seen within one window — fine for typical
# deployments, swept at moderate intervals to cap memory.
_EVICT_THRESHOLD = 10_000
_SWEEP_INTERVAL_HITS = 500  # sweep every N hits when above the threshold


def _now() -> float:
    """Monotonic clock seam (overridable in tests)."""
    return monotonic()


def reset() -> None:
    """Drop all in-process counters (test isolation)."""
    _buckets.clear()


_hit_counter: int = 0


def _sweep(now: float, window_seconds: float) -> int:
    cutoff = now - window_seconds
    stale = [k for k, v in list(_buckets.items()) if not v or v[-1] < cutoff]
    for k in stale:
        _buckets.pop(k, None)
    return len(stale)


def _allow_in_process(key: str, limit: int, window_seconds: float) -> bool:
    global _hit_counter
    history = _buckets[key]
    now = _now()
    cutoff = now - window_seconds
    while history and history[0] < cutoff:
        history.popleft()
    if len(history) >= limit:
        return False
    history.append(now)
    _hit_counter += 1
    if len(_buckets) > _EVICT_THRESHOLD and _hit_counter % _SWEEP_INTERVAL_HITS == 0:
        _sweep(now, window_seconds)
    return True


# Atomic INCR + TTL guarantee. A separate INCR-then-EXPIRE is not atomic: if
# EXPIRE never lands (Redis blip, or the process dies between the two calls) the
# key persists with no TTL and, once its count passes the limit, throttles that
# identifier forever. This script increments and (re)sets the TTL whenever the
# key lacks one, so a counter can never get stuck without an expiry — even if a
# prior attempt left a TTL-less key behind.
_RL_LUA = """
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

# Whether the limiter is currently degraded to the per-process fallback because
# Redis is unreachable. Tracked so the transition is logged once, not per hit.
_redis_degraded = False


async def _allow_redis(key: str, limit: int, window_seconds: int) -> bool | None:
    """Redis fixed-window counter. Returns None when Redis is unavailable so
    the caller can fall back to the in-process limiter."""
    global _redis_degraded
    import app.redis_client as _rc

    rl_key = f"noodle:rl:{key}"
    try:
        count = await _rc.redis_client.eval(_RL_LUA, 1, rl_key, window_seconds)
        if _redis_degraded:
            logger.warning("rate-limit: Redis counter restored; resuming shared limits")
            _redis_degraded = False
        return int(count) <= limit
    except Exception:  # noqa: BLE001 - Redis down → in-process fallback
        if not _redis_degraded:
            logger.warning(
                "rate-limit: Redis unavailable; falling back to per-process "
                "counters. Limits are enforced per replica until Redis recovers."
            )
            _redis_degraded = True
        return None


async def allow(
    bucket: str,
    identifier: str,
    *,
    limit: int,
    window_seconds: int = 60,
) -> bool:
    """Return True if a hit on ``(bucket, identifier)`` is within ``limit`` per
    ``window_seconds``; False when it should be throttled. ``limit <= 0``
    disables the limit (always allows)."""
    if limit <= 0:
        return True
    key = f"{bucket}:{identifier}"
    if settings.queue_backend == "redis":
        result = await _allow_redis(key, limit, window_seconds)
        if result is not None:
            return result
    return _allow_in_process(key, limit, window_seconds)
