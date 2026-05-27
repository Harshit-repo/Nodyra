"""Targeted tests for ``RuntimePool`` idle-reaping logic.

These tests don't spawn real subprocesses — they exercise the pool's
bookkeeping with a stand-in process object so the reaper logic stays fast
and deterministic.
"""

import time
from dataclasses import dataclass, field

import pytest

from app.services.runtime_pool import _EnvPool


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
