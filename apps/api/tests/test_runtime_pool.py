"""Targeted tests for ``RuntimePool`` idle-reaping logic.

These tests don't spawn real subprocesses — they exercise the pool's
bookkeeping with a stand-in process object so the reaper logic stays fast
and deterministic.
"""

import asyncio
import time
from dataclasses import dataclass, field

import pytest

from app.config import settings
from app.services.runtime_pool import RuntimePool, _EnvPool, _RssBudget

@dataclass(eq=False)  # default identity-based hash so the pool's set works
class _FakeProcess:
    """Mimics the bits of ``_RuntimeProcess`` the pool inspects."""

    dead: bool = False
    idle_since: float = 0.0
    closed: bool = False

    # The pool checks ``proc.process.returncode``; expose a tiny proxy.
    process: object = field(
        default_factory=lambda: type("P", (), {"returncode": None})()
    )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_reap_idle_closes_old_processes() -> None:
    pool = _EnvPool(env_id=None, min_size=0, max_size=3)
    fresh = _FakeProcess(idle_since=time.time())
    stale = _FakeProcess(idle_since=time.time() - 3600)
    # Put both directly into idle; bypass acquire which would spawn.
    pool._idle = [fresh, stale]  # noqa: SLF001
    pool._all = {fresh, stale}  # noqa: SLF001

    closed = await pool.reap_idle(threshold_seconds=60)
    assert closed == 1
    assert stale.closed is True
    assert fresh.closed is False
    assert pool._idle == [fresh]  # noqa: SLF001


@pytest.mark.asyncio
async def test_reap_idle_disabled_when_threshold_is_zero() -> None:
    pool = _EnvPool(env_id=None, min_size=0, max_size=3)
    stale = _FakeProcess(idle_since=time.time() - 9_999)
    pool._idle = [stale]  # noqa: SLF001
    pool._all = {stale}  # noqa: SLF001

    closed = await pool.reap_idle(threshold_seconds=0)
    assert closed == 0
    assert stale.closed is False


@pytest.mark.asyncio
async def test_subworkflow_slot_throttles_then_releases() -> None:
    """A bounded slot lets exactly cap holders in at once and frees on exit."""
    orig_cap = settings.max_concurrent_subworkflows
    settings.max_concurrent_subworkflows = 1
    try:
        pool = RuntimePool()
        async with pool.subworkflow_slot():
            # Cap is 1 and we hold it, so the sem is now locked.
            assert pool._subworkflow_sem.locked() is True  # noqa: SLF001
        # Released on context exit.
        assert pool._subworkflow_sem.locked() is False  # noqa: SLF001
    finally:
        settings.max_concurrent_subworkflows = orig_cap


@pytest.mark.asyncio
async def test_subworkflow_slot_soft_cap_proceeds_on_timeout() -> None:
    """When no slot frees within the timeout, the call proceeds anyway
    (soft cap) so nested sub-workflows never deadlock."""
    orig_cap = settings.max_concurrent_subworkflows
    orig_timeout = settings.subworkflow_spawn_timeout_seconds
    settings.max_concurrent_subworkflows = 1
    settings.subworkflow_spawn_timeout_seconds = 0.05
    try:
        pool = RuntimePool()
        async with pool.subworkflow_slot():
            # Cap exhausted; this nested acquire must still enter (no deadlock).
            entered = False
            async with pool.subworkflow_slot():
                entered = True
            assert entered is True
    finally:
        settings.max_concurrent_subworkflows = orig_cap
        settings.subworkflow_spawn_timeout_seconds = orig_timeout


@pytest.mark.asyncio
async def test_global_slot_bounds_concurrency() -> None:
    """``global_slot`` exposes the ``max_concurrent_runs`` ceiling."""
    orig = settings.max_concurrent_runs
    settings.max_concurrent_runs = 1
    try:
        pool = RuntimePool()
        async with pool.global_slot():
            assert pool.global_slot().locked() is True
        assert pool.global_slot().locked() is False
    finally:
        settings.max_concurrent_runs = orig


@pytest.mark.asyncio
async def test_capacity_probe_reflects_global_slot() -> None:
    """``has_immediate_capacity`` / ``available_global_slots`` track the sem."""
    orig = settings.max_concurrent_runs
    settings.max_concurrent_runs = 2
    try:
        pool = RuntimePool()
        assert pool.has_immediate_capacity() is True
        assert pool.available_global_slots() == 2
        async with pool.global_slot():
            assert pool.available_global_slots() == 1
            assert pool.has_immediate_capacity() is True
            async with pool.global_slot():
                assert pool.available_global_slots() == 0
                assert pool.has_immediate_capacity() is False
        assert pool.has_immediate_capacity() is True
    finally:
        settings.max_concurrent_runs = orig


@pytest.mark.asyncio
async def test_rss_budget_disabled_when_budget_zero() -> None:
    """Budget 0 (or unknown estimate) means the gate never blocks."""
    budget = _RssBudget()
    async with budget.reserve(estimate=999, budget=0):
        assert budget.committed_bytes == 0  # no-op, nothing committed
    async with budget.reserve(estimate=0, budget=1000):
        assert budget.committed_bytes == 0


@pytest.mark.asyncio
async def test_rss_budget_admits_single_over_budget_run() -> None:
    """A lone run is always admitted even if it alone exceeds the budget
    (forward-progress guarantee)."""
    budget = _RssBudget()
    async with budget.reserve(estimate=5000, budget=1000):
        assert budget.committed_bytes == 5000
    assert budget.committed_bytes == 0


@pytest.mark.asyncio
async def test_rss_budget_blocks_until_headroom_frees() -> None:
    """A second reservation that would exceed the budget waits until the
    first releases."""
    budget = _RssBudget()
    order: list[str] = []

    async def first() -> None:
        async with budget.reserve(estimate=700, budget=1000):
            order.append("first-in")
            await asyncio.sleep(0.05)
            order.append("first-out")

    async def second() -> None:
        # Let `first` reserve before we try.
        await asyncio.sleep(0.01)
        async with budget.reserve(estimate=700, budget=1000):
            order.append("second-in")

    await asyncio.gather(first(), second())
    # 700 + 700 > 1000, so second must wait for first to release.
    assert order == ["first-in", "first-out", "second-in"]
    assert budget.committed_bytes == 0
