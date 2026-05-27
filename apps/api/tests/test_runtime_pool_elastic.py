"""Slice 17 — elastic _EnvPool behaviour across the three user-facing presets.

These tests don't spawn real subprocesses. They drive ``_EnvPool``
bookkeeping through stand-in process objects so the elastic-pool semantics
(release-close when min_size==0, reaper respects min_size, acquire grows
up to max_size) stay fast and deterministic.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import pytest

from app.services.runtime_pool import _EnvPool


@dataclass(eq=False)
class _FakeProcess:
    """Mimics the bits of ``_RuntimeProcess`` the pool inspects."""

    dead: bool = False
    idle_since: float = 0.0
    closed: bool = False
    process: object = field(
        default_factory=lambda: type("P", (), {"returncode": None})()
    )

    async def close(self) -> None:
        self.closed = True


# --- Fixed preset (regression) -----------------------------------------------


@pytest.mark.asyncio
async def test_fixed_release_keeps_worker_warm() -> None:
    """``min_size == max_size``: release pools the worker; reaper preserves floor."""
    pool = _EnvPool(env_id=None, min_size=2, max_size=2)
    proc = _FakeProcess()
    pool._all.add(proc)  # noqa: SLF001 - simulate prior acquire

    pool.release(proc)
    assert proc.closed is False
    assert pool._idle == [proc]  # noqa: SLF001

    # Reaper must NOT close the only warm worker when min_size protects it.
    proc.idle_since = time.time() - 10_000
    closed = await pool.reap_idle(threshold_seconds=60)
    assert closed == 0
    assert proc.closed is False


# --- Elastic preset ----------------------------------------------------------


@pytest.mark.asyncio
async def test_elastic_release_pools_worker_under_max() -> None:
    pool = _EnvPool(env_id=None, min_size=1, max_size=4)
    proc = _FakeProcess()
    pool._all.add(proc)  # noqa: SLF001

    pool.release(proc)
    assert proc.closed is False
    assert pool._idle == [proc]  # noqa: SLF001


@pytest.mark.asyncio
async def test_elastic_reaper_respects_min_floor() -> None:
    """Reaper closes surplus past ``min_size``, oldest-idle first."""
    pool = _EnvPool(env_id=None, min_size=1, max_size=4)
    now = time.time()
    # 3 stale processes; floor is 1, so reaper may close 2.
    p1 = _FakeProcess(idle_since=now - 1000)
    p2 = _FakeProcess(idle_since=now - 2000)
    p3 = _FakeProcess(idle_since=now - 3000)
    pool._idle = [p1, p2, p3]  # noqa: SLF001
    pool._all = {p1, p2, p3}  # noqa: SLF001

    closed = await pool.reap_idle(threshold_seconds=60)
    assert closed == 2
    # Oldest-idle (p3, p2) should be closed first; freshest (p1) kept.
    assert p3.closed is True
    assert p2.closed is True
    assert p1.closed is False
    assert pool._idle == [p1]  # noqa: SLF001


# --- Spawn-per-run preset ----------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_per_run_release_closes_worker_immediately() -> None:
    """``min_size == 0``: release fires close(); worker is not pooled."""
    pool = _EnvPool(env_id=None, min_size=0, max_size=4)
    proc = _FakeProcess()
    pool._all.add(proc)  # noqa: SLF001

    pool.release(proc)
    # release() schedules close() on the loop — give it a tick.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert proc.closed is True
    assert proc not in pool._all  # noqa: SLF001
    assert pool._idle == []  # noqa: SLF001


@pytest.mark.asyncio
async def test_spawn_per_run_reaper_is_noop_with_no_idle() -> None:
    """Spawn-per-run keeps nothing idle, so the reaper has nothing to do."""
    pool = _EnvPool(env_id=None, min_size=0, max_size=4)
    closed = await pool.reap_idle(threshold_seconds=60)
    assert closed == 0


# --- Constructor invariants --------------------------------------------------


@pytest.mark.asyncio
async def test_max_size_at_least_min_size() -> None:
    """If a bad ``(min, max)`` slips through, the pool normalises max upward."""
    pool = _EnvPool(env_id=None, min_size=3, max_size=1)
    assert pool.max_size == 3
    assert pool.min_size == 3
