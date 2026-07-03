"""Dispatcher liveness signal (program A6).

Each dispatch-loop tick, the owning process records a heartbeat for the
providers it leases (``worker`` -> local/docker, ``control`` -> agent/kubernetes,
``inline`` -> all). The Runner Pools health endpoint reads these to tell whether
a pool's runs can actually be dispatched — surfacing the "no dispatcher
reachable" state that otherwise leaves agent runs queued indefinitely with no
visible cause.

Backed by Redis when configured (so the API can see the worker's heartbeat
across processes); falls back to an in-process record so single-process
(``inline``) dev still reports correctly.
"""

from __future__ import annotations

import time

from app.config import settings

_ALL_PROVIDERS = ("local", "docker", "agent", "kubernetes")

PROVIDERS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "inline": _ALL_PROVIDERS,
    "worker": ("local", "docker"),
    "control": ("agent", "kubernetes"),
    "disabled": (),
}

# ~3x the default dispatch poll interval, so one missed tick doesn't flap.
HEARTBEAT_TTL_SECONDS = 30

_KEY = "nodyra:dispatcher:{provider}"
_local: dict[str, float] = {}  # provider -> epoch ts (in-process fallback)


def providers_for_role(role: str) -> tuple[str, ...]:
    return PROVIDERS_BY_ROLE.get(role, ())


async def record_heartbeat(role: str) -> None:
    """Mark every provider this role dispatches as alive for the TTL window."""
    providers = providers_for_role(role)
    if not providers:
        return
    now = time.time()
    if settings.queue_backend == "redis":
        try:
            from app.redis_client import redis_client  # noqa: PLC0415

            pipe = redis_client.pipeline()
            for provider in providers:
                pipe.set(
                    _KEY.format(provider=provider),
                    str(now),
                    ex=HEARTBEAT_TTL_SECONDS,
                )
            await pipe.execute()
            return
        except Exception:  # noqa: BLE001 - fall back to in-process record
            pass
    for provider in providers:
        _local[provider] = now


async def live_providers() -> set[str]:
    """The providers a dispatcher is currently leasing (fresh heartbeat)."""
    if settings.queue_backend == "redis":
        try:
            from app.redis_client import redis_client  # noqa: PLC0415

            found: set[str] = set()
            for provider in _ALL_PROVIDERS:
                if await redis_client.get(_KEY.format(provider=provider)):
                    found.add(provider)
            return found
        except Exception:  # noqa: BLE001
            pass
    now = time.time()
    return {
        provider
        for provider, ts in _local.items()
        if now - ts < HEARTBEAT_TTL_SECONDS
    }


def reset_local() -> None:
    """Test helper: clear the in-process heartbeat record."""
    _local.clear()
