"""B4: host-owned process isolation. Pools are keyed per environment,
reused per key, and evicted only when idle with no in-flight tasks —
a long-running code node must never have its pool reaped mid-task."""

import time

from nodyra.process_isolation import PooledProcessIsolator


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
