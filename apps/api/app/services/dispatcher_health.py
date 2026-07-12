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

import json
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
_DISPATCHER_KEY = "nodyra:dispatcher-instance:{worker_id}"
_local: dict[str, float] = {}  # provider -> epoch ts (in-process fallback)
_local_dispatchers: dict[str, dict] = {}


def providers_for_role(role: str) -> tuple[str, ...]:
    return PROVIDERS_BY_ROLE.get(role, ())


async def record_heartbeat(
    role: str,
    *,
    worker_id: str | None = None,
    labels: dict[str, str] | None = None,
    available_slots: int | None = None,
    max_slots: int | None = None,
) -> None:
    """Mark every provider this role dispatches as alive for the TTL window."""
    providers = providers_for_role(role)
    if not providers:
        return
    now = time.time()
    dispatcher_payload = {
        "id": worker_id or role,
        "role": role,
        "providers": list(providers),
        "labels": labels or {},
        "available_slots": available_slots,
        "max_slots": max_slots,
        "last_seen": now,
    }
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
            if worker_id:
                pipe.set(
                    _DISPATCHER_KEY.format(worker_id=worker_id),
                    json.dumps(dispatcher_payload),
                    ex=HEARTBEAT_TTL_SECONDS,
                )
            await pipe.execute()
            return
        except Exception:  # noqa: BLE001 - fall back to in-process record
            pass
    for provider in providers:
        _local[provider] = now
    if worker_id:
        _local_dispatchers[worker_id] = dispatcher_payload


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
    return {provider for provider, ts in _local.items() if now - ts < HEARTBEAT_TTL_SECONDS}


async def live_dispatchers() -> list[dict]:
    """Fresh dispatcher instances with role, providers, labels, and capacity."""
    if settings.queue_backend == "redis":
        try:
            from app.redis_client import redis_client  # noqa: PLC0415

            rows: list[dict] = []
            async for key in redis_client.scan_iter(_DISPATCHER_KEY.format(worker_id="*")):
                raw = await redis_client.get(key)
                if raw is None:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(str(raw))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
            return rows
        except Exception:  # noqa: BLE001
            pass
    now = time.time()
    return [
        payload
        for payload in _local_dispatchers.values()
        if now - float(payload.get("last_seen") or 0) < HEARTBEAT_TTL_SECONDS
    ]


def reset_local() -> None:
    """Test helper: clear the in-process heartbeat record."""
    _local.clear()
    _local_dispatchers.clear()
