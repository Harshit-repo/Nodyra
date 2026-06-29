"""Multi-replica coordination: heartbeat registration and discovery.

Each API/worker replica registers a heartbeat in Redis.  A polling
endpoint reads them back so operators can see which replicas are alive
and how the workload is distributed.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid

from app.redis_client import redis_client

logger = logging.getLogger(__name__)

REPLICA_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
_HEARTBEAT_KEY = "noodle:replicas:heartbeats"
_HEARTBEAT_TTL = 30  # seconds — must be refreshed before expiry


async def register_heartbeat(*, role: str = "api") -> None:
    """Upsert this replica's heartbeat with a TTL.  Call every 15 s."""
    try:
        # Use time.time() (Unix epoch) so timestamps are comparable across
        # replicas — asyncio.get_event_loop().time() is a per-process monotonic
        # clock with an arbitrary origin and cannot be compared cross-replica.
        await redis_client.hset(
            _HEARTBEAT_KEY,
            REPLICA_ID,
            f"{role}|{time.time():.3f}",
        )
        await redis_client.expire(_HEARTBEAT_KEY, _HEARTBEAT_TTL + 10)
    except Exception:  # noqa: BLE001
        logger.debug("replica heartbeat failed (Redis unreachable?)")


async def list_replicas() -> list[dict]:
    """Return all currently-registered replicas."""
    try:
        raw = await redis_client.hgetall(_HEARTBEAT_KEY)
    except Exception:  # noqa: BLE001
        return [{"replica_id": REPLICA_ID, "role": "api", "status": "unknown"}]

    replicas: list[dict] = []
    now = time.time()
    for rid_bytes, val_bytes in (raw or {}).items():
        rid = rid_bytes.decode() if isinstance(rid_bytes, bytes) else str(rid_bytes)
        val = val_bytes.decode() if isinstance(val_bytes, bytes) else str(val_bytes)
        parts = val.split("|")
        role = parts[0] if parts else "unknown"
        ts = float(parts[1]) if len(parts) > 1 else 0
        age = now - ts
        replicas.append({
            "replica_id": rid,
            "role": role,
            "status": "alive" if age < _HEARTBEAT_TTL + 15 else "stale",
            "last_seen_seconds_ago": round(age, 1),
        })
    return sorted(replicas, key=lambda r: r["replica_id"])


async def replica_heartbeat_loop(role: str = "api") -> None:
    """Background loop that refreshes this replica's heartbeat."""
    while True:
        try:
            await register_heartbeat(role=role)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(15)
