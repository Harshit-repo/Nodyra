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
import contextvars
import functools
import importlib
import logging
import multiprocessing
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from concurrent.futures.process import ProcessPoolExecutor

_logger = logging.getLogger(__name__)

# Hosts (runner.py) set this to the workflow's environment id so each
# environment gets its own ProcessPoolExecutor and worker state cannot
# bleed across environments. None is the shared default bucket used by
# tests and the in-process dev path.
pool_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "nodyra_pool_key", default=None
)


def _picklable_artifact_store() -> Any:
    """The current artifact store, or None when it cannot cross a process.

    A remote/streaming store may hold sockets or file handles. Checking here
    keeps the failure at "datasets are unavailable in the worker", which is
    the status quo, rather than turning it into an unpicklable-argument crash
    that takes the whole node with it.
    """
    try:
        import pickle

        from nodyra.context import artifact_store

        store = artifact_store.get()
        if store is None:
            return None
        pickle.dumps(store)
        return store
    except Exception:  # noqa: BLE001 - diagnosis must never break dispatch
        _logger.debug("artifact store cannot cross into a worker", exc_info=True)
        return None


def _call_with_artifact_store(store: Any, fn: Callable[..., Any], kwargs: dict) -> Any:
    """Run ``fn`` in this worker with ``store`` installed as its artifact store.

    Module-level so it pickles. ``run_in_executor`` carries no contextvars
    across the process boundary, so without this the worker's
    ``artifact_store`` is unset and every dataset/artifact helper raises
    "datasets are not available in this execution context" — including in a
    Code node, which the engine deliberately hands DatasetRefs on the
    understanding that it can read them.

    A store that fails to travel is not worth failing the node for: the node
    runs anyway, exactly as it did before, and only dataset access inside it
    raises.
    """
    try:
        from nodyra.context import artifact_store

        # Set unconditionally, including to None. Workers are reused across
        # runs, so a call that brings no store must clear the previous run's
        # rather than inherit it — one run writing into another run's
        # artifact directory is a worse failure than no dataset access.
        artifact_store.set(store)
    except Exception:  # noqa: BLE001 - never block the node on this
        _logger.debug("could not install artifact store in worker", exc_info=True)
    return fn(**kwargs)


class ProcessIsolator(Protocol):
    """Runs a synchronous node function outside the calling process."""

    async def run(
        self,
        fn: Callable[..., Any],
        kwargs: dict[str, Any],
        *,
        timeout: float | None,
    ) -> Any: ...


class InlineProcessIsolator:
    """Runs node functions in the calling process, on a worker thread.

    Used by the runtime worker (``nodyra_runtime``): that process is already
    the isolation boundary — the host spawns one disposable worker per
    environment and kills it on wedge, timeout, or rebuild. Spawning a second
    layer of ``ProcessPoolExecutor`` children inside the worker wedged on
    Windows (HK-2): the spawn child inherits the worker's stdin pipe while
    the host-callback reader thread is blocked on it, so the child's spawn
    bootstrap deadlocks and code nodes hang until their timeout.
    """

    async def run(
        self,
        fn: Callable[..., Any],
        kwargs: dict[str, Any],
        *,
        timeout: float | None,
    ) -> Any:
        if timeout is not None:
            return await asyncio.wait_for(asyncio.to_thread(fn, **kwargs), timeout)
        return await asyncio.to_thread(fn, **kwargs)


class PooledProcessIsolator:
    """One ProcessPoolExecutor per isolation key (environment id).

    A pool is evicted only when it has no in-flight tasks AND has seen no
    submit/completion activity for ``idle_seconds``. Timeouts and broken
    pools evict immediately so the next attempt gets a fresh pool."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        idle_seconds: float = 600.0,
        max_total_workers: int = 32,
    ) -> None:
        self._max_workers = max_workers
        self._idle_seconds = idle_seconds
        # E-11: global cap on total worker processes across all pools.
        # At max_workers=4 the default allows up to 8 active environments
        # before idle pools are evicted.
        self._max_total_workers = max_total_workers
        self._pools: dict[str | None, ProcessPoolExecutor] = {}
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
        broken_pool_error = importlib.import_module("concurrent.futures.process").BrokenProcessPool
        loop = asyncio.get_running_loop()
        # Carry the caller's artifact store across the process boundary, so a
        # node that reads a DatasetRef works in here as it does elsewhere.
        # A store that cannot be pickled is dropped rather than failing the
        # node — that is the behaviour this code has always had.
        store = _picklable_artifact_store()
        future = loop.run_in_executor(
            pool, functools.partial(_call_with_artifact_store, store, fn, kwargs)
        )
        try:
            if timeout is not None:
                return await asyncio.wait_for(future, timeout)
            return await future
        except TimeoutError:
            self.evict(key)  # kill the wedged worker; next run gets a fresh pool
            raise
        except broken_pool_error as exc:
            self.evict(key)
            raise ValueError(
                "code node crashed: subprocess died (possible "
                "out-of-memory, segfault, or unpicklable value)"
            ) from exc
        finally:
            self._checkin(key)

    def _checkout(self, key: str | None) -> "ProcessPoolExecutor":
        with self._mutex:
            now = time.monotonic()
            idle = [
                k
                for k, last in self._last_activity.items()
                if k != key and self._in_flight.get(k, 0) == 0 and now - last > self._idle_seconds
            ]
            for k in idle:
                self._evict_locked(k)
            pool = self._pools.get(key)
            if pool is None:
                # E-11: enforce global worker cap by evicting the idlest idle
                # pool before creating a new one.  If every pool is busy we
                # exceed the cap temporarily rather than block a live run.
                max_pools = max(1, self._max_total_workers // max(1, self._max_workers))
                if len(self._pools) >= max_pools:
                    idle_candidates = sorted(
                        (last, k)
                        for k, last in self._last_activity.items()
                        if self._in_flight.get(k, 0) == 0
                    )
                    if idle_candidates:
                        _, oldest = idle_candidates[0]
                        _logger.warning(
                            "process isolator: evicting idle pool %r to stay under "
                            "max_total_workers=%d (E-11)",
                            oldest,
                            self._max_total_workers,
                        )
                        self._evict_locked(oldest)
                # Resolve from the currently registered submodule instead of
                # concurrent.futures' lazy top-level alias. Test/plugin module
                # isolation can evict and re-import the submodule; retaining
                # the old alias then gives multiprocessing a stale
                # ``_process_worker`` and every submission fails to pickle.
                process_module = importlib.import_module("concurrent.futures.process")
                pool = process_module.ProcessPoolExecutor(
                    max_workers=self._max_workers,
                    # Python 3.14 changed the POSIX default from ``fork`` to
                    # ``forkserver``. Pin ``spawn`` so execution semantics are
                    # deterministic across supported platforms and workers
                    # never inherit unsafe thread/connection state from the
                    # API host. Pools are warm, so startup cost is amortized.
                    mp_context=multiprocessing.get_context("spawn"),
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
            processes = list((getattr(pool, "_processes", None) or {}).values())
            pool.shutdown(wait=False, cancel_futures=True)
            for proc in processes:
                if proc.is_alive():
                    proc.terminate()
            for proc in processes:
                proc.join(timeout=1.0)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1.0)

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
