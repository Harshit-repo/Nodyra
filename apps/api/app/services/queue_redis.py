"""Redis-backed queue optimization layer.

When ``queue_backend=redis`` and Redis is reachable, this module provides
O(1) lease/dequeue operations that avoid DB contention under concurrent
workers.  The DB remains the source of truth — every state transition is
flushed to ``RunQueueEntry`` (async, best-effort on the hot path,
reconciled at startup).

Keys::

    noodle:queue:queued           Sorted Set  (score=priority, member=run_id)
    noodle:queue:lease:<run_id>   String      (worker_id, TTL=LEASE_TTL)
    noodle:queue:status:<run_id>  String      (queued|leased|running|...)
    noodle:queue:stats            Hash        (counters by status)
"""

from __future__ import annotations

import logging

from app.redis_client import redis_client

logger = logging.getLogger(__name__)

LEASE_TTL = 30  # seconds — matches DB DEFAULT_LEASE_SECONDS

# Lua script: atomically pop the highest-priority entry from the queued set
# and mark it as leased.  Returns the run_id or nil.
_LEASE_LUA = """
local run_id = redis.call('ZPOPMAX', KEYS[1])
if run_id and run_id[1] then
    local rid = run_id[1]
    redis.call('SET', KEYS[2] .. rid, ARGV[1], 'EX', ARGV[2])
    redis.call('SET', KEYS[3] .. rid, 'leased')
    redis.call('HINCRBY', KEYS[4], 'queued', -1)
    redis.call('HINCRBY', KEYS[4], 'leased', 1)
    return rid
end
return nil
"""


async def enqueue(run_id: str, *, priority: int = 0) -> None:
    """Add a run to the Redis queue."""
    try:
        async with redis_client.pipeline() as pipe:
            pipe.zadd("noodle:queue:queued", {run_id: priority})
            pipe.set(f"noodle:queue:status:{run_id}", "queued")
            pipe.hincrby("noodle:queue:stats", "queued", 1)
            await pipe.execute()
    except Exception:  # noqa: BLE001 — DB is the fallback
        logger.debug("redis enqueue failed run_id=%s", run_id)


async def lease(worker_id: str) -> str | None:
    """Atomically lease the highest-priority queued run.  Returns run_id or None."""
    try:
        result = await redis_client.eval(
            _LEASE_LUA, 4,
            "noodle:queue:queued",
            "noodle:queue:lease:",
            "noodle:queue:status:",
            "noodle:queue:stats",
            worker_id,
            str(LEASE_TTL),
        )
        return result if result else None
    except Exception:  # noqa: BLE001
        logger.debug("redis lease failed")
        return None


async def heartbeat(run_id: str, worker_id: str) -> bool:
    """Extend the lease TTL.  Returns True if the lease is still held."""
    try:
        key = f"noodle:queue:lease:{run_id}"
        current = await redis_client.get(key)
        if current and current.decode() == worker_id:
            await redis_client.expire(key, LEASE_TTL)
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


async def complete(run_id: str) -> None:
    """Mark a run as completed."""
    try:
        async with redis_client.pipeline() as pipe:
            pipe.delete(f"noodle:queue:lease:{run_id}")
            pipe.set(f"noodle:queue:status:{run_id}", "completed")
            pipe.hincrby("noodle:queue:stats", "leased", -1)
            pipe.hincrby("noodle:queue:stats", "completed", 1)
            await pipe.execute()
    except Exception:  # noqa: BLE001
        logger.debug("redis complete failed run_id=%s", run_id)


async def fail(run_id: str, *, dead_letter: bool = False) -> None:
    """Mark a run as failed or dead-lettered."""
    status = "dead_lettered" if dead_letter else "failed"
    try:
        async with redis_client.pipeline() as pipe:
            pipe.delete(f"noodle:queue:lease:{run_id}")
            pipe.set(f"noodle:queue:status:{run_id}", status)
            pipe.hincrby("noodle:queue:stats", "leased", -1)
            pipe.hincrby("noodle:queue:stats", status, 1)
            await pipe.execute()
    except Exception:  # noqa: BLE001
        logger.debug("redis fail failed run_id=%s", run_id)


async def cancel(run_id: str) -> None:
    """Remove a run from the queue entirely."""
    try:
        async with redis_client.pipeline() as pipe:
            pipe.zrem("noodle:queue:queued", run_id)
            pipe.delete(f"noodle:queue:lease:{run_id}")
            pipe.delete(f"noodle:queue:status:{run_id}")
            await pipe.execute()
    except Exception:  # noqa: BLE001
        logger.debug("redis cancel failed run_id=%s", run_id)


async def requeue_expired_leases() -> list[str]:
    """Scan for expired leases and return their run_ids for requeue."""
    expired: list[str] = []
    try:
        # Scan lease keys — this is O(N) but only called periodically
        # and lease count is bounded by concurrent worker count.
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(
                cursor, match="noodle:queue:lease:*", count=100
            )
            for key in keys:
                rid = key.decode().removeprefix("noodle:queue:lease:")
                ttl = await redis_client.ttl(key)
                if ttl <= 0:
                    # Atomically delete the key — only requeue if the delete
                    # succeeds (returns 1).  A heartbeat that extended the key
                    # between our SCAN and TTL calls would have changed TTL to
                    # a positive value, so we would not reach this branch.  But
                    # if the key expired naturally (TTL → -2) between our TTL
                    # call and DELETE, delete returns 0 and we skip it safely.
                    deleted = await redis_client.delete(key)
                    if deleted:
                        expired.append(rid)
            if cursor == 0:
                break
    except Exception:  # noqa: BLE001
        logger.debug("redis requeue scan failed")
    return expired


async def stats() -> dict[str, int]:
    """Return queue stats from Redis."""
    try:
        raw = await redis_client.hgetall("noodle:queue:stats")
        result: dict[str, int] = {
            "queued": 0, "leased": 0, "running": 0, "waiting": 0,
            "completed": 0, "failed": 0, "dead_lettered": 0, "cancelled": 0,
        }
        for k, v in (raw or {}).items():
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = int(v.decode() if isinstance(v, bytes) else str(v))
            if key in result:
                result[key] = val
        result["queued"] = int(
            await redis_client.zcard("noodle:queue:queued") or 0
        )
        return result
    except Exception:  # noqa: BLE001
        return {}
