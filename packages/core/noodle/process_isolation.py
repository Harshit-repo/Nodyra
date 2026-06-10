"""Host-owned process isolation for engine nodes (B4).

The engine no longer owns ProcessPoolExecutors. Hosts construct a
ProcessIsolator and inject it via ``execute(..., process_isolator=...)``;
when none is injected (unit tests, exported scripts) a lazily created module
default is used.

Eviction is keyed off task *completion* and in-flight counts: a pool running
a long code node is never reaped mid-task. The old engine-owned pools were
swept by last-``get`` time, so another environment requesting a pool could
kill a 30-minute code node halfway through.
"""

import asyncio
import concurrent.futures
import contextvars
import functools
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

# Hosts (runner.py) set this to the workflow's environment id so each
# environment gets its own ProcessPoolExecutor and worker state cannot
# bleed across environments. None is the shared default bucket used by
# tests and the in-process dev path.
pool_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "noodle_pool_key", default=None
)


class ProcessIsolator(Protocol):
    """Runs a synchronous node function outside the calling process."""

    async def run(
        self,
        fn: Callable[..., Any],
        kwargs: dict[str, Any],
        *,
        timeout: float | None,
    ) -> Any: ...


class PooledProcessIsolator:
    """One ProcessPoolExecutor per isolation key (environment id).

    A pool is evicted only when it has no in-flight tasks AND has seen no
    submit/completion activity for ``idle_seconds``. Timeouts and broken
    pools evict immediately so the next attempt gets a fresh pool."""

    def __init__(self, *, max_workers: int = 4, idle_seconds: float = 600.0) -> None:
        self._max_workers = max_workers
        self._idle_seconds = idle_seconds
        self._pools: dict[str | None, concurrent.futures.ProcessPoolExecutor] = {}
        self._last_activity: dict[str | None, float] = {}
        self._in_flight: dict[str | None, int] = {}
        self._mutex = threading.Lock()

    async def run(
        self,
        fn: Callable[..., Any],
        kwargs: dict[str, Any],
        *,
        timeout: float | None,
    ) -> Any:
        key = pool_key.get()
        pool = self._checkout(key)
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(pool, functools.partial(fn, **kwargs))
        try:
            if timeout is not None:
                return await asyncio.wait_for(future, timeout)
            return await future
        except TimeoutError:
            self.evict(key)  # kill the wedged worker; next run gets a fresh pool
            raise
        except concurrent.futures.process.BrokenProcessPool as exc:
            self.evict(key)
            raise ValueError(
                "code node crashed: subprocess died (possible "
                "out-of-memory, segfault, or unpicklable value)"
            ) from exc
        finally:
            self._checkin(key)

    def _checkout(self, key: str | None) -> concurrent.futures.ProcessPoolExecutor:
        with self._mutex:
            now = time.monotonic()
            idle = [
                k for k, last in self._last_activity.items()
                if k != key
                and self._in_flight.get(k, 0) == 0
                and now - last > self._idle_seconds
            ]
            for k in idle:
                self._evict_locked(k)
            pool = self._pools.get(key)
            if pool is None:
                pool = concurrent.futures.ProcessPoolExecutor(
                    max_workers=self._max_workers
                )
                self._pools[key] = pool
            self._in_flight[key] = self._in_flight.get(key, 0) + 1
            self._last_activity[key] = now
            return pool

    def _checkin(self, key: str | None) -> None:
        with self._mutex:
            self._in_flight[key] = max(0, self._in_flight.get(key, 0) - 1)
            self._last_activity[key] = time.monotonic()

    def evict(self, key: str | None) -> None:
        with self._mutex:
            self._evict_locked(key)

    def _evict_locked(self, key: str | None) -> None:
        pool = self._pools.pop(key, None)
        self._last_activity.pop(key, None)
        self._in_flight.pop(key, None)
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def shutdown(self) -> None:
        with self._mutex:
            for key in list(self._pools):
                self._evict_locked(key)


_default: PooledProcessIsolator | None = None


def default_isolator() -> PooledProcessIsolator:
    """Lazily created fallback for callers that don't inject an isolator
    (unit tests, exported scripts)."""
    global _default
    if _default is None:
        _default = PooledProcessIsolator()
    return _default
