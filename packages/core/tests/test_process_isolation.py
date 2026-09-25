"""B4: host-owned process isolation. Pools are keyed per environment,
reused per key, and evicted only when idle with no in-flight tasks —
a long-running code node must never have its pool reaped mid-task."""

import concurrent.futures
import importlib
import os
import sys
import time

from nodyra.process_isolation import PooledProcessIsolator


def test_pool_start_method_is_cross_platform_spawn():
    iso = PooledProcessIsolator()
    try:
        pool = iso._checkout(None)
        assert pool._mp_context.get_start_method() == "spawn"
        iso._checkin(None)
    finally:
        iso.shutdown()


async def test_pool_survives_process_module_reimport():
    """Pool creation must not retain concurrent.futures' stale lazy alias."""
    module_name = "concurrent.futures.process"
    stale_module = importlib.import_module(module_name)
    stale_package_attr = concurrent.futures.process
    try:
        del sys.modules[module_name]
        del concurrent.futures.process
        fresh_module = importlib.import_module(module_name)
        assert fresh_module is not stale_module

        iso = PooledProcessIsolator(max_workers=1)
        try:
            child_pid = await iso.run(os.getpid, {}, timeout=30)
            assert isinstance(child_pid, int)
            assert child_pid != os.getpid()
        finally:
            iso.shutdown()
    finally:
        sys.modules[module_name] = stale_module
        concurrent.futures.process = stale_package_attr


def test_same_key_reuses_pool():
    iso = PooledProcessIsolator()
    try:
        a1 = iso._checkout("env-alpha")
        iso._checkin("env-alpha")
        a2 = iso._checkout("env-alpha")
        iso._checkin("env-alpha")
        assert a1 is a2
    finally:
        iso.shutdown()


def test_different_keys_get_different_pools():
    iso = PooledProcessIsolator()
    try:
        a = iso._checkout("env-x")
        iso._checkin("env-x")
        b = iso._checkout("env-y")
        iso._checkin("env-y")
        assert a is not b
    finally:
        iso.shutdown()


def test_none_key_is_its_own_pool():
    iso = PooledProcessIsolator()
    try:
        none_pool = iso._checkout(None)
        iso._checkin(None)
        named = iso._checkout("env-z")
        iso._checkin("env-z")
        assert none_pool is not named
    finally:
        iso.shutdown()


def test_idle_pool_is_evicted_on_next_checkout():
    iso = PooledProcessIsolator(idle_seconds=0.01)
    try:
        iso._checkout("idle-env")
        iso._checkin("idle-env")
        iso._last_activity["idle-env"] = time.monotonic() - 700
        iso._checkout("active-env")
        iso._checkin("active-env")
        assert "idle-env" not in iso._pools
    finally:
        iso.shutdown()


def test_in_flight_pool_survives_idle_sweep():
    """The cold-pool fix: a pool with a running task is never reaped, no
    matter how stale its last-activity timestamp looks."""
    iso = PooledProcessIsolator(idle_seconds=0.01)
    try:
        busy = iso._checkout("busy-env")  # in flight — no checkin yet
        iso._last_activity["busy-env"] = time.monotonic() - 700
        iso._checkout("other-env")
        iso._checkin("other-env")
        assert iso._pools.get("busy-env") is busy  # survived the sweep

        iso._checkin("busy-env")  # task completes
        iso._last_activity["busy-env"] = time.monotonic() - 700
        iso._checkout("other-env2")
        iso._checkin("other-env2")
        assert "busy-env" not in iso._pools  # idle now → reaped
    finally:
        iso.shutdown()


# ---------------------------------------------------------------------------
# The artifact store has to cross into the worker.
#
# run_in_executor carries no contextvars, so without this the worker's
# artifact_store is unset and every dataset helper raises "datasets are not
# available in this execution context" — including inside a Code node, which
# the engine deliberately hands DatasetRefs on the understanding that it can
# read them.


def _read_store_run_id() -> str | None:
    from nodyra.context import artifact_store

    store = artifact_store.get()
    return getattr(store, "run_id", None) if store is not None else None


def test_the_store_is_installed_before_the_node_runs(tmp_path) -> None:
    from nodyra.artifacts import LocalArtifactStore
    from nodyra.process_isolation import _call_with_artifact_store

    store = LocalArtifactStore(tmp_path, run_id="run-xyz")

    seen = _call_with_artifact_store(store, _read_store_run_id, {})

    assert seen == "run-xyz"


def test_no_store_still_runs_the_node() -> None:
    """Plenty of callers — unit tests, exported scripts — have no store."""
    from nodyra.process_isolation import _call_with_artifact_store

    assert _call_with_artifact_store(None, _read_store_run_id, {}) is None


def test_a_reused_worker_does_not_inherit_the_previous_store(tmp_path) -> None:
    """Workers outlive a run. A call bringing no store must clear the last
    one, or a later run writes into an earlier run's artifact directory."""
    from nodyra.artifacts import LocalArtifactStore
    from nodyra.process_isolation import _call_with_artifact_store

    first = LocalArtifactStore(tmp_path, run_id="run-first")
    assert _call_with_artifact_store(first, _read_store_run_id, {}) == "run-first"

    assert _call_with_artifact_store(None, _read_store_run_id, {}) is None


def test_the_current_store_is_offered_when_it_can_travel(tmp_path) -> None:
    from nodyra.artifacts import LocalArtifactStore
    from nodyra.context import artifact_store
    from nodyra.process_isolation import _picklable_artifact_store

    token = artifact_store.set(LocalArtifactStore(tmp_path, run_id="run-abc"))
    try:
        assert getattr(_picklable_artifact_store(), "run_id", None) == "run-abc"
    finally:
        artifact_store.reset(token)


class _Unpicklable:
    run_id = "nope"

    def __reduce__(self):
        raise TypeError("this store cannot be pickled")


def test_a_store_that_cannot_travel_is_dropped_not_raised() -> None:
    """Losing dataset access in the worker is the old behaviour. Losing the
    node itself to an unpicklable-argument crash would be worse."""
    from nodyra.context import artifact_store
    from nodyra.process_isolation import _picklable_artifact_store

    token = artifact_store.set(_Unpicklable())
    try:
        assert _picklable_artifact_store() is None
    finally:
        artifact_store.reset(token)
