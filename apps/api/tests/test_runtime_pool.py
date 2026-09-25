"""Targeted tests for ``RuntimePool`` idle-reaping logic.

These tests don't spawn real subprocesses — they exercise the pool's
bookkeeping with a stand-in process object so the reaper logic stays fast
and deterministic.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field

import pytest
from httpx import AsyncClient

from app.config import settings
from app.services.runtime_pool import RuntimePool, _EnvPool, _RssBudget, _RuntimeProcess


@dataclass(eq=False)  # default identity-based hash so the pool's set works
class _FakeProcess:
    """Mimics the bits of ``_RuntimeProcess`` the pool inspects."""

    dead: bool = False
    idle_since: float = 0.0
    closed: bool = False

    # The pool checks ``proc.process.returncode``; expose a tiny proxy.
    process: object = field(default_factory=lambda: type("P", (), {"returncode": None})())

    async def close(self) -> None:
        self.closed = True


async def test_dispatch_deadline_returns_timed_out_and_releases_process(client, monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from app.services import runtime_pool

    async def slow_run(*args, **kwargs):
        await asyncio.Event().wait()

    proc = _FakeProcess()
    proc.run = slow_run
    env = Mock(rss_estimate=0)
    env.acquire = AsyncMock(return_value=proc)
    pool = RuntimePool()
    monkeypatch.setattr(pool, "_env_pool", AsyncMock(return_value=env))
    monkeypatch.setattr(runtime_pool, "_rss_soft_budget_bytes", AsyncMock(return_value=0))
    monkeypatch.setattr(runtime_pool, "_resolve_run_org", AsyncMock(return_value="default"))
    monkeypatch.setattr(runtime_pool, "_org_run_limits_for", AsyncMock(return_value={}))
    events = []

    async def on_event(event):
        events.append(event)

    status = await pool.dispatch("timeout-run", None, {}, None, None, on_event, run_timeout=0.01)
    assert status == "timed_out"
    assert proc.closed
    env.release.assert_called_once_with(proc)
    assert events[0]["type"] == "run_error"
    assert "timed out" in events[0]["error"]


class _ProtocolStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    async def drain(self) -> None:
        pass


class _ProtocolStdout:
    def __init__(self) -> None:
        self.lines: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.lines.get()

    def feed(self, event: dict) -> None:
        self.lines.put_nowait(json.dumps(event).encode() + b"\n")


class _ProtocolProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.stdin = _ProtocolStdin()
        self.stdout = _ProtocolStdout()
        self.stderr = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode or 0)


async def _protocol_request_id(process: _ProtocolProcess) -> str:
    for _ in range(100):
        if process.stdin.writes:
            return str(json.loads(process.stdin.writes[0])["request_id"])
        await asyncio.sleep(0.001)
    raise AssertionError("runtime host did not write a run request")


@pytest.mark.asyncio
async def test_runtime_process_consumes_heartbeat_without_forwarding(monkeypatch) -> None:
    monkeypatch.setattr(settings, "runtime_heartbeat_timeout_seconds", 0.2)
    monkeypatch.setattr(settings, "runtime_no_progress_timeout_seconds", 1.0)
    process = _ProtocolProcess()
    runtime = _RuntimeProcess(process, env_id=None)
    events: list[dict] = []

    task = asyncio.create_task(runtime.run("run-1", {}, None, None, events.append))
    request_id = await _protocol_request_id(process)
    process.stdout.feed({"type": "heartbeat", "request_id": request_id})
    process.stdout.feed({"type": "result", "request_id": request_id, "status": "success"})

    assert await task == "success"
    assert events == []
    assert not runtime.dead


@pytest.mark.asyncio
async def test_runtime_process_missing_heartbeat_is_closed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "runtime_heartbeat_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "runtime_no_progress_timeout_seconds", 0.0)
    process = _ProtocolProcess()
    runtime = _RuntimeProcess(process, env_id=None)

    with pytest.raises(RuntimeError, match="heartbeat lost"):
        await runtime.run("run-1", {}, None, None, lambda _event: None)

    assert runtime.dead
    assert process.terminated


@pytest.mark.asyncio
async def test_runtime_process_heartbeats_do_not_mask_no_progress(monkeypatch) -> None:
    # The point of this test is that a heartbeat arriving *after* the
    # no-progress deadline does not reset it. Only the ordering of the two
    # deadlines matters, so give the heartbeat timeout a wide margin: at 0.2s
    # it sat 140ms from the 0.06s sleep below, and under full-suite load the
    # sleep overshoots, the heartbeat timeout fires first, and the test fails
    # claiming "heartbeat lost" instead of "no protocol progress".
    monkeypatch.setattr(settings, "runtime_heartbeat_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "runtime_no_progress_timeout_seconds", 0.05)
    process = _ProtocolProcess()
    runtime = _RuntimeProcess(process, env_id=None)

    task = asyncio.create_task(runtime.run("run-1", {}, None, None, lambda _event: None))
    request_id = await _protocol_request_id(process)
    await asyncio.sleep(0.06)
    process.stdout.feed({"type": "heartbeat", "request_id": request_id})

    with pytest.raises(RuntimeError, match="no protocol progress"):
        await task
    assert runtime.dead
    assert process.terminated


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
async def test_subworkflow_slot_is_per_org_under_multi_tenancy(monkeypatch) -> None:
    """C4: one org exhausting its sub-workflow budget must not consume the
    spawn budget of another org."""
    from app.services import runtime_pool as rp_module
    from app.tenancy import current_org_id

    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)

    async def _cap(org_id: str) -> int:
        return 1  # every org gets exactly one slot

    monkeypatch.setattr(rp_module, "_org_subworkflow_cap", _cap)
    pool = RuntimePool()

    token = current_org_id.set("org-a")
    try:
        async with pool.subworkflow_slot():
            assert pool._org_subworkflow_sems["org-a"].locked()  # noqa: SLF001
            # org-b's budget is untouched while org-a is saturated.
            current_org_id.set("org-b")
            async with pool.subworkflow_slot():
                assert pool._org_subworkflow_sems["org-b"].locked()  # noqa: SLF001
            assert not pool._org_subworkflow_sems["org-b"].locked()  # noqa: SLF001
    finally:
        current_org_id.reset(token)
    assert not pool._org_subworkflow_sems["org-a"].locked()  # noqa: SLF001


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
async def test_resize_up_grants_exact_requested_capacity() -> None:
    orig = settings.max_concurrent_runs
    settings.max_concurrent_runs = 2
    try:
        pool = RuntimePool()
        assert await pool.resize(4) == 4
        assert pool.current_max_slots() == 4
        assert pool.available_global_slots() == 4
        async with pool.global_slot():
            assert pool.available_global_slots() == 3
    finally:
        settings.max_concurrent_runs = orig


@pytest.mark.asyncio
async def test_resize_down_drains_before_admitting_new_work() -> None:
    orig = settings.max_concurrent_runs
    settings.max_concurrent_runs = 2
    try:
        pool = RuntimePool()
        both_started = asyncio.Event()
        third_started = asyncio.Event()
        release = [asyncio.Event(), asyncio.Event()]
        admitted: list[int] = []

        async def hold(index: int) -> None:
            async with pool.global_slot():
                admitted.append(index)
                if len(admitted) == 2:
                    both_started.set()
                if index == 2:
                    third_started.set()
                else:
                    await release[index].wait()

        first = asyncio.create_task(hold(0))
        second = asyncio.create_task(hold(1))
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert await pool.resize(1) == 1
        assert pool.available_global_slots() == 0

        third = asyncio.create_task(hold(2))
        await asyncio.sleep(0.05)
        assert admitted == [0, 1]

        release[0].set()
        await first
        await asyncio.sleep(0.05)
        assert admitted == [0, 1]

        release[1].set()
        await second
        await asyncio.wait_for(third_started.wait(), timeout=1)
        await third
        assert admitted == [0, 1, 2]
        assert pool.available_global_slots() == 1
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


# ---------------------------------------------------------------------------
# Phase 1: per-environment runtime flags (PYTHON_JIT / PYTHON_LAZY_IMPORTS)
# ---------------------------------------------------------------------------


async def test_resolve_env_runtime_flags_reads_configured_flags(client: AsyncClient) -> None:
    """A DB row with runtime_flags={"jit": True} resolves to {"jit": True}."""
    from app.services.runtime_pool import _resolve_env_runtime_flags

    created = (
        await client.post(
            "/environments",
            json={"name": "Flagged pool env", "runtime_flags": {"jit": True}},
        )
    ).json()

    flags = await _resolve_env_runtime_flags(created["id"])
    assert flags == {"jit": True}


async def test_resolve_env_runtime_flags_empty_dict_for_no_flags(client: AsyncClient) -> None:
    from app.services.runtime_pool import _resolve_env_runtime_flags

    created = (await client.post("/environments", json={"name": "Unflagged"})).json()
    flags = await _resolve_env_runtime_flags(created["id"])
    assert flags == {}


async def test_resolve_env_runtime_flags_none_env_id_returns_empty() -> None:
    from app.services.runtime_pool import _resolve_env_runtime_flags

    assert await _resolve_env_runtime_flags(None) == {}


async def test_resolve_env_runtime_flags_missing_env_returns_empty(client: AsyncClient) -> None:
    from app.services.runtime_pool import _resolve_env_runtime_flags

    assert await _resolve_env_runtime_flags("does-not-exist") == {}


async def test_resolve_env_runtime_flags_db_error_returns_empty(monkeypatch) -> None:
    """A DB failure degrades to {} — flags are accelerators, never a reason to
    fail a dispatch."""
    from app.services import runtime_pool as rp_module

    class _BoomSession:
        async def __aenter__(self):
            raise RuntimeError("db unavailable")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(rp_module, "SessionLocal", lambda: _BoomSession())
    assert await rp_module._resolve_env_runtime_flags("some-env") == {}


class _FakeReadable:
    """Stand-in for a subprocess stdout/stderr stream in ``_RuntimeProcess.spawn``."""

    def __init__(self, first_line: bytes = b"") -> None:
        self._lines = [first_line] if first_line else []

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeSpawnedProcess:
    """Stand-in for ``asyncio.subprocess.Process`` capturing the env kwarg."""

    def __init__(self, env: dict) -> None:
        self.env = env
        self.stdin = _FakeReadable()
        self.stdout = _FakeReadable(b'{"type": "ready"}\n')
        self.stderr = None

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        return None


async def test_spawn_injects_jit_env_var_from_runtime_flags(monkeypatch) -> None:
    """Env row's runtime_flags={"jit": True} reaches the spawned subprocess's
    env as PYTHON_JIT=='1', with no PYTHON_LAZY_IMPORTS."""
    import app.services.runtime_pool as rp_module

    captured: dict = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.update(kwargs.get("env") or {})
        return _FakeSpawnedProcess(kwargs.get("env") or {})

    async def fake_python_for_env(env_id):
        return "/usr/bin/python3"

    async def fake_resolve_flags(env_id):
        return {"jit": True}

    monkeypatch.setattr(rp_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(rp_module, "_python_for_env", fake_python_for_env)
    monkeypatch.setattr(rp_module, "_resolve_env_runtime_flags", fake_resolve_flags)

    await rp_module._RuntimeProcess.spawn("some-env")

    assert captured.get("PYTHON_JIT") == "1"
    assert "PYTHON_LAZY_IMPORTS" not in captured


async def test_spawn_no_flags_sets_no_accelerator_env_vars(monkeypatch) -> None:
    import app.services.runtime_pool as rp_module

    captured: dict = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.update(kwargs.get("env") or {})
        return _FakeSpawnedProcess(kwargs.get("env") or {})

    async def fake_python_for_env(env_id):
        return "/usr/bin/python3"

    async def fake_resolve_flags(env_id):
        return {}

    monkeypatch.setattr(rp_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(rp_module, "_python_for_env", fake_python_for_env)
    monkeypatch.setattr(rp_module, "_resolve_env_runtime_flags", fake_resolve_flags)

    await rp_module._RuntimeProcess.spawn("some-env")

    assert "PYTHON_JIT" not in captured
    assert "PYTHON_LAZY_IMPORTS" not in captured


async def test_spawn_does_not_leak_host_python_jit_when_flag_absent(monkeypatch) -> None:
    """The host API process's own PYTHON_JIT must never leak into a worker;
    only the per-env flag (resolved from the DB) is a source of truth."""
    import app.services.runtime_pool as rp_module

    monkeypatch.setenv("PYTHON_JIT", "1")  # host process opts in; workers must not inherit it

    captured: dict = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.update(kwargs.get("env") or {})
        return _FakeSpawnedProcess(kwargs.get("env") or {})

    async def fake_python_for_env(env_id):
        return "/usr/bin/python3"

    async def fake_resolve_flags(env_id):
        return {}

    monkeypatch.setattr(rp_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(rp_module, "_python_for_env", fake_python_for_env)
    monkeypatch.setattr(rp_module, "_resolve_env_runtime_flags", fake_resolve_flags)

    await rp_module._RuntimeProcess.spawn("some-env")

    assert "PYTHON_JIT" not in captured


@pytest.mark.asyncio
async def test_env_pool_drain_closes_idle_workers_and_invalidates_inflight() -> None:
    """Rebuilding an environment must not be blocked by warm workers — and
    workers released after the drain must not serve the new environment."""
    from app.services.runtime_pool import _EnvPool

    pool = _EnvPool(env_id="e1", min_size=1, max_size=2, rss_estimate=0)

    idle = _FakeProcess()
    idle.idle_since = time.time() - 30
    idle.generation = 0
    inflight = _FakeProcess()
    inflight.generation = 0

    pool._all.add(idle)
    pool._all.add(inflight)
    pool._idle.append(idle)

    await pool.drain()

    assert idle.closed is True
    assert inflight.closed is False  # in-flight runs are never killed
    assert pool._idle == []
    assert inflight in pool._all

    # The in-flight worker finishing after the drain must be closed, not warmed.
    pool.release(inflight)
    assert inflight not in pool._all
    assert inflight not in pool._idle
    # release() fires close as a background task; give it a tick.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_env_pool_release_keeps_current_generation_worker_warm() -> None:
    from app.services.runtime_pool import _EnvPool

    pool = _EnvPool(env_id="e1", min_size=1, max_size=2, rss_estimate=0)
    proc = _FakeProcess()
    proc.generation = 0
    pool._all.add(proc)
    pool.release(proc)
    assert pool._idle == [proc]
    assert proc.closed is False


@pytest.mark.asyncio
async def test_env_pool_drain_force_closes_inflight_and_pauses(monkeypatch) -> None:
    """A rebuild must not wait on wedged/long in-flight runs: force-drain
    terminates them, and acquire() parks runs until the pool is unpaused."""
    import app.services.runtime_pool as rp_module
    from app.services.runtime_pool import _EnvPool, _PoolPaused

    pool = _EnvPool(env_id="e1", min_size=1, max_size=2, rss_estimate=0)
    inflight = _FakeProcess()
    inflight.generation = 0
    pool._all.add(inflight)

    await pool.drain(force=True)

    assert inflight.closed is True
    assert pool._all == set()
    with pytest.raises(_PoolPaused):
        await pool.acquire()

    await pool.unpause()
    fresh = _FakeProcess()
    monkeypatch.setattr(
        rp_module._RuntimeProcess, "spawn", _AsyncSpawn(fresh).spawn
    )
    proc = await pool.acquire()
    assert proc is fresh
    pool.release(proc)


class _AsyncSpawn:
    def __init__(self, proc: _FakeProcess) -> None:
        self._proc = proc

    async def spawn(self, env_id: str) -> _FakeProcess:
        return self._proc


@pytest.mark.asyncio
async def test_runtime_pool_drain_env_targets_only_that_env() -> None:
    pool = RuntimePool()
    env_a = _EnvPool(env_id="env-a", min_size=1, max_size=2, rss_estimate=0)
    env_b = _EnvPool(env_id="env-b", min_size=1, max_size=2, rss_estimate=0)
    worker_a = _FakeProcess()
    worker_a.generation = 0
    worker_b = _FakeProcess()
    worker_b.generation = 0
    env_a._idle.append(worker_a)
    env_a._all.add(worker_a)
    env_b._idle.append(worker_b)
    env_b._all.add(worker_b)
    pool._envs["env-a"] = env_a
    pool._envs["env-b"] = env_b

    await pool.drain_env("env-a")

    assert worker_a.closed is True
    assert worker_b.closed is False
