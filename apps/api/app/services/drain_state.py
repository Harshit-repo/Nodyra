"""Cluster-wide durable-queue drain state.

The API and execution workers are separate processes in the production
topology. A process-local setting therefore cannot implement the operator
promise made by ``/ops/drain``. Redis is already mandatory for that topology,
so it is the coordination point; local mode retains the in-process fast path.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

_DRAIN_KEY = "nodyra:queue:draining"
_shared_drain_cache = False


async def set_draining(draining: bool) -> bool:
    """Set drain state locally and, in split mode, for every replica.

    A failed shared write is surfaced to the caller rather than claiming a
    drain that workers never observed. The local flag changes only after the
    Redis write succeeds.
    """

    global _shared_drain_cache
    value = bool(draining)
    if settings.queue_backend == "redis":
        await redis_client.set(_DRAIN_KEY, "1" if value else "0")
    _shared_drain_cache = value
    settings.queue_drain = value
    return value


async def is_draining() -> bool:
    """Return effective local/cluster drain state.

    A process-local SIGTERM drain always wins. In Redis mode, an unavailable
    coordination backend fails closed: dispatch stops leasing until Redis can
    prove the cluster is not draining. This avoids silently defeating an
    operator drain during a control-plane partition.
    """

    global _shared_drain_cache
    if settings.queue_drain:
        return True
    if settings.queue_backend != "redis":
        return False
    try:
        raw = await redis_client.get(_DRAIN_KEY)
    except Exception:  # noqa: BLE001 - coordination failure must fail closed
        logger.warning("Redis drain-state read failed; pausing new queue leases")
        return True
    if raw is None:
        _shared_drain_cache = False
    else:
        _shared_drain_cache = str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return _shared_drain_cache


def reset_local_cache() -> None:
    """Test/lifespan helper; does not mutate shared Redis state."""

    global _shared_drain_cache
    _shared_drain_cache = False
