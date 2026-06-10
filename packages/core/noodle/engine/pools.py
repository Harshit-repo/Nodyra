"""Per-environment process pools for process-isolated node types.

NOTE: this module is deleted in B4 (Task 3) — pool ownership moves to
``noodle.process_isolation`` with host injection. Do not extend it."""

import concurrent.futures
import contextvars
import time


# Per-environment-key pool dict. Keyed by an opaque string (env id) so that
# code nodes from different environments cannot share worker state. None is the
# default bucket used when no key is set (in-process tests, legacy callers).
_process_pools: dict[str | None, concurrent.futures.ProcessPoolExecutor] = {}
# Tracks the last time each pool was actually used so idle pools can be reaped.
_pool_last_used: dict[str | None, float] = {}
# Seconds a process pool is allowed to be idle before the next _get_process_pool
# call evicts it. Mirrors runner_idle_seconds (default 600s) at the engine layer.
_POOL_IDLE_SECONDS: float = 600.0


def _get_process_pool(
    max_workers: int = 4,
    *,
    key: str | None = None,
) -> concurrent.futures.ProcessPoolExecutor:
    """Return (or create) the process pool for the given isolation key.

    Each distinct key gets its own pool so worker state (imported modules,
    patched globals) cannot leak between environments. ``key=None`` is the
    shared default used by tests and the in-process dev path.

    Idle pools (no activity for ``_POOL_IDLE_SECONDS``) are evicted before
    returning a pool so the dict does not accumulate indefinitely — one pool
    per environment-id means O(envs * max_workers) background processes on a
    busy server, quickly exhausting process/FD limits.
    """
    now = time.monotonic()
    # Sweep idle pools before potentially creating a new one.
    idle_keys = [
        k for k, last in _pool_last_used.items()
        if now - last > _POOL_IDLE_SECONDS and k != key
    ]
    for k in idle_keys:
        _evict_pool(k)

    pool = _process_pools.get(key)
    if pool is None:
        pool = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
        _process_pools[key] = pool
    _pool_last_used[key] = now
    return pool


def _evict_pool(key: str | None) -> None:
    """Shutdown and remove the pool for ``key`` so the next use gets a fresh one."""
    pool = _process_pools.pop(key, None)
    _pool_last_used.pop(key, None)
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# Callers (runner.py) set this to the workflow's environment id so each
# environment gets its own ProcessPoolExecutor and worker state cannot
# bleed across environments. Defaults to None (shared pool, legacy path).
pool_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "noodle_pool_key", default=None
)
