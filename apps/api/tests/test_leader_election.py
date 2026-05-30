"""Tests for the scheduler/retention leader-election helper."""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_hold_leader_lock_sqlite_always_acquires() -> None:
    """Under SQLite the helper short-circuits — there's only one writer."""
    from app.services.leader_election import hold_leader_lock

    async with hold_leader_lock("test.lock") as acquired:
        assert acquired is True


@pytest.mark.asyncio
async def test_stable_key_is_signed_64bit() -> None:
    from app.services.leader_election import _stable_key

    # Same input -> same key (the whole point is determinism across replicas).
    assert _stable_key("noodle.scheduler") == _stable_key("noodle.scheduler")
    # Keys differ between locks so scheduler/retention don't collide.
    assert _stable_key("noodle.scheduler") != _stable_key("noodle.retention")
    # Must fit in a Postgres bigint (signed 64-bit).
    key = _stable_key("noodle.scheduler")
    assert -(2**63) <= key < 2**63


@pytest.mark.asyncio
async def test_run_with_leader_election_invokes_loop_when_acquired() -> None:
    """If the lock is acquired, the inner loop runs exactly once per
    acquisition (it's expected to be long-lived itself)."""
    from app.services import leader_election

    calls = 0

    async def loop_once() -> None:
        nonlocal calls
        calls += 1
        # Yield to the loop so cancellation can land — and so the wrapper
        # keeps re-entering the acquisition cycle.
        await asyncio.sleep(0.01)

    task = asyncio.create_task(
        leader_election.run_with_leader_election(
            loop_once, name="noodle.test", retry_seconds=0.01
        )
    )
    # Let it re-acquire a few times.
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert calls >= 1


@pytest.mark.asyncio
async def test_run_with_leader_election_retries_on_failed_acquire(monkeypatch) -> None:
    """If ``hold_leader_lock`` yields ``False`` the wrapper sleeps and retries,
    it does not invoke the loop."""
    from contextlib import asynccontextmanager

    from app.services import leader_election

    attempts = 0

    @asynccontextmanager
    async def fake_hold(name):
        nonlocal attempts
        attempts += 1
        yield False

    monkeypatch.setattr(leader_election, "hold_leader_lock", fake_hold)

    loop_called = False

    async def loop() -> None:
        nonlocal loop_called
        loop_called = True

    task = asyncio.create_task(
        leader_election.run_with_leader_election(
            loop, name="noodle.test2", retry_seconds=0.01
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert loop_called is False
    assert attempts >= 2
