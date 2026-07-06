"""Docker-worker autoscaling planner + spawn/remove/reconcile."""

import os
import tempfile
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401 - registers ORM models on Base.metadata
from app.db import Base
from app.models import Runner, RunnerPool
from app.services.docker_workers import (
    PoolState,
    RemoveRunner,
    RunnerState,
    SpawnRunner,
    plan_scaling,
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    engine = create_async_engine(f"sqlite+aiosqlite:///{handle.name}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
    try:
        os.unlink(handle.name)
    except OSError:
        pass


def _pool(**kw):
    base = dict(
        pool_id="p1",
        queued=0,
        runners=[],
        enabled=True,
        min_runners=0,
        max_runners=4,
        idle_seconds=300,
    )
    base.update(kw)
    return PoolState(**base)


def _runner(rid="r1", current=0, idle=0.0, draining=False, online=True, max_concurrent_runs=2):
    return RunnerState(
        runner_id=rid,
        current_runs=current,
        idle_seconds=idle,
        draining=draining,
        online=online,
        max_concurrent_runs=max_concurrent_runs,
    )


def test_scale_up_on_backlog_no_runners():
    actions = plan_scaling([_pool(queued=3)])
    assert actions == [SpawnRunner("p1")]


def test_scale_up_when_all_saturated():
    actions = plan_scaling([_pool(queued=5, runners=[_runner(current=2, max_concurrent_runs=2)], max_runners=4)])
    # max_concurrent per runner is 2 → saturated → spawn one more
    assert actions == [SpawnRunner("p1")]


def test_no_scale_up_when_capacity_free():
    actions = plan_scaling([_pool(queued=5, runners=[_runner(current=0)])])
    assert actions == []


def test_respect_max_runners():
    runners = [_runner(rid=f"r{i}", current=2, max_concurrent_runs=2) for i in range(4)]
    actions = plan_scaling([_pool(queued=9, runners=runners, max_runners=4)])
    assert actions == []


def test_scale_down_idle_runner():
    r = _runner(current=0, idle=600, draining=True)
    actions = plan_scaling([_pool(queued=0, runners=[r], min_runners=0)])
    assert actions == [RemoveRunner("r1")]


def test_no_scale_down_when_busy():
    r = _runner(current=1, idle=600, draining=True)
    assert plan_scaling([_pool(queued=0, runners=[r])]) == []


def test_respect_min_runners():
    r = _runner(current=0, idle=600, draining=True)
    assert plan_scaling([_pool(runners=[r], min_runners=1)]) == []


def test_disabled_pool_no_actions():
    assert plan_scaling([_pool(queued=9, enabled=False)]) == []


def test_clamp_min_over_max():
    # min > max → treated as max; no spawn beyond max
    runners = [_runner(rid=f"r{i}", current=2, max_concurrent_runs=2) for i in range(2)]
    actions = plan_scaling([_pool(queued=9, runners=runners, min_runners=5, max_runners=2)])
    assert actions == []


def test_one_action_per_pool_per_tick():
    actions = plan_scaling([_pool(queued=99, runners=[_runner(current=2, max_concurrent_runs=2)], max_runners=8)])
    assert len(actions) == 1


class FakeImages:
    def __init__(self, existing=()):
        self._have = set(existing)
        self.built = []

    def get(self, tag):
        if tag not in self._have:
            raise KeyError(tag)
        return object()

    def build(self, **kw):
        self.built.append(kw)
        self._have.add(kw.get("tag"))
        return (object(), iter(()))


class FakeContainer:
    def __init__(self, name, labels, owner):
        self.name = name
        self.labels = labels or {}
        self._owner = owner
        self.removed = False

    def remove(self, force=False):
        self.removed = True
        self._owner._by_name.pop(self.name, None)


class FakeContainers:
    def __init__(self):
        self.run_calls = []
        self._by_name = {}

    def run(self, image, **kw):
        self.run_calls.append({"image": image, **kw})
        name = kw.get("name")
        c = FakeContainer(name, kw.get("labels") or {}, self)
        self._by_name[name] = c
        return c

    def add_orphan(self, name, labels):
        """Seed a container that has no matching Runner row (crash orphan)."""
        self._by_name[name] = FakeContainer(name, labels, self)

    def get(self, name):
        if name not in self._by_name:
            raise KeyError(name)
        return self._by_name[name]

    def list(self, all=False, filters=None):  # noqa: A002 - mirror docker SDK kwarg
        # Loosely honour a label filter so reconcile's filtered list is realistic.
        conts = list(self._by_name.values())
        want = (filters or {}).get("label") or []
        for f in want:
            if "=" in f:
                k, v = f.split("=", 1)
                conts = [c for c in conts if c.labels.get(k) == v]
        return conts


class FakeDockerClient:
    def __init__(self, existing_images=()):
        self.images = FakeImages(existing_images)
        self.containers = FakeContainers()

    def info(self):
        return {"Runtimes": {"runc": {}}}


async def test_ensure_agent_image_builds_when_absent(monkeypatch):
    from app.services import docker_workers

    async def _fake_wheels(*a, **k):
        from pathlib import Path
        return [Path("nodyra_core.whl")]

    monkeypatch.setattr(docker_workers, "ensure_wheels", _fake_wheels)
    monkeypatch.setattr(docker_workers, "_agent_build_context", lambda wheels: b"ctx")
    client = FakeDockerClient()
    tag = await docker_workers.ensure_agent_image(client)
    assert tag == docker_workers.agent_image_tag()
    assert client.images.built  # built once
    # Cache hit: second call does not rebuild
    client.images.built.clear()
    await docker_workers.ensure_agent_image(client)
    assert not client.images.built


async def test_spawn_docker_runner_sets_caps_and_labels(session, monkeypatch):
    from app.services import docker_workers

    pool = RunnerPool(
        name="dw", provider="agent",
        provider_config={
            "docker_runner": {"cpu": 1.0, "memory_mb": 512, "pids": 256,
                              "max_concurrent_runs": 2, "sandbox": True},
            "docker_api_url": "http://host.docker.internal:8000",
        },
    )
    session.add(pool)
    await session.commit()

    client = FakeDockerClient(existing_images=(docker_workers.agent_image_tag(),))
    runner = await docker_workers.spawn_docker_runner(session, pool, client=client)

    assert runner.capabilities["docker_managed"] is True
    assert runner.capabilities["sandbox"] is True
    call = client.containers.run_calls[0]
    assert call["nano_cpus"] == 1_000_000_000
    assert call["mem_limit"] == "512m"
    assert call["pids_limit"] == 256
    assert call["labels"]["nodyra.pool"] == pool.id
    assert call["labels"]["nodyra.runner"] == runner.id
    # sandbox=True → docker socket mounted into the runner
    assert any("docker.sock" in str(v) for v in call["volumes"])
    assert call["environment"]["NODYRA_RUNNER_TOKEN"]


async def test_spawn_no_socket_when_sandbox_off(session):
    from app.services import docker_workers
    pool = RunnerPool(name="dw2", provider="agent",
                      provider_config={"docker_runner": {"sandbox": False},
                                       "docker_api_url": "http://x:8000"})
    session.add(pool)
    await session.commit()
    client = FakeDockerClient(existing_images=(docker_workers.agent_image_tag(),))
    await docker_workers.spawn_docker_runner(session, pool, client=client)
    call = client.containers.run_calls[0]
    assert not call.get("volumes")


async def test_remove_busy_runner_refused(session):
    from app.services import docker_workers
    pool = RunnerPool(name="dw3", provider="agent", provider_config={})
    session.add(pool)
    await session.commit()
    r = Runner(pool_id=pool.id, name="r", status="online", current_runs=1,
               capabilities={"docker_managed": True, "container_name": "c"})
    session.add(r)
    await session.commit()
    client = FakeDockerClient()
    with pytest.raises(docker_workers.RunnerBusy):
        await docker_workers.remove_docker_runner(session, r, client=client)


async def test_build_pool_states_counts_queue_and_runners(session):
    from app.models import RunQueueEntry
    from app.services import docker_workers
    pool = RunnerPool(name="dw", provider="agent",
                      provider_config={"docker_autoscale": {"enabled": True,
                          "min_runners": 0, "max_runners": 3, "idle_seconds": 300}})
    session.add(pool)
    await session.commit()
    session.add(Runner(pool_id=pool.id, name="r", status="online",
                       current_runs=0, capabilities={"docker_managed": True}))
    session.add(RunQueueEntry(run_id="x", workflow_id="w",
                              runner_pool_id=pool.id, status="queued"))
    await session.commit()
    states = await docker_workers._build_pool_states(session)
    assert len(states) == 1
    assert states[0].queued == 1
    assert states[0].enabled is True
    assert len(states[0].runners) == 1


async def test_remove_docker_runner_refuses_when_daemon_unreachable(session):
    """A runner WITH a container but NO reachable daemon must not have its row
    deleted — that would orphan a live container. reconcile cleans it up later."""
    from app.services import docker_workers
    pool = RunnerPool(name="dw", provider="agent", provider_config={})
    session.add(pool)
    await session.commit()
    r = Runner(pool_id=pool.id, name="r", status="online", current_runs=0,
               capabilities={"docker_managed": True, "container_name": "nodyra-worker-abc"})
    session.add(r)
    await session.commit()
    with pytest.raises(docker_workers.DaemonUnreachable):
        await docker_workers.remove_docker_runner(session, r, client=None)
    # Row survives for reconcile.
    assert await session.get(Runner, r.id) is not None


async def test_reconcile_reaps_dead_row_and_orphan_container(session):
    """A docker-managed row whose container is gone → row deleted; a container
    labelled for the pool with no row → container removed."""
    from app.services import docker_workers
    pool = RunnerPool(name="dw", provider="agent", provider_config={})
    session.add(pool)
    await session.commit()
    # Dead row: has a container_name, but the daemon has no such container.
    dead = Runner(pool_id=pool.id, name="dead", status="offline", current_runs=0,
                  capabilities={"docker_managed": True, "container_name": "gone-123"})
    session.add(dead)
    await session.commit()

    client = FakeDockerClient()
    # Orphan container labelled for this pool, no matching row.
    client.containers.add_orphan(
        "nodyra-worker-orphan",
        {"nodyra.managed": "true", "nodyra.pool": pool.id, "nodyra.runner": "no-such-row"},
    )
    acted = await docker_workers.reconcile(session, client, pool)
    assert acted == 2
    assert await session.get(Runner, dead.id) is None  # dead row reaped
    assert "nodyra-worker-orphan" not in client.containers._by_name  # orphan removed


async def test_reconcile_keeps_row_with_live_container(session):
    from app.services import docker_workers
    pool = RunnerPool(name="dw", provider="agent", provider_config={})
    session.add(pool)
    await session.commit()
    r = Runner(pool_id=pool.id, name="live", status="online", current_runs=0,
               capabilities={"docker_managed": True, "container_name": "nodyra-worker-live"})
    session.add(r)
    await session.commit()
    client = FakeDockerClient()
    client.containers.add_orphan(
        "nodyra-worker-live",
        {"nodyra.pool": pool.id, "nodyra.runner": r.id},
    )
    acted = await docker_workers.reconcile(session, client, pool)
    assert acted == 0
    assert await session.get(Runner, r.id) is not None


async def test_reconcile_spares_mid_spawn_row_without_container_name(session):
    """A freshly-minted row (no container_name yet) inside the grace window must
    NOT be reaped — it is mid-spawn."""
    from app.services import docker_workers
    pool = RunnerPool(name="dw", provider="agent", provider_config={})
    session.add(pool)
    await session.commit()
    spawning = Runner(pool_id=pool.id, name="spawning", status="offline", current_runs=0,
                      capabilities={"docker_managed": True})  # no container_name
    session.add(spawning)
    await session.commit()
    client = FakeDockerClient()
    acted = await docker_workers.reconcile(session, client, pool)
    assert acted == 0
    assert await session.get(Runner, spawning.id) is not None


def test_plan_scaling_capacity_not_collapsed_by_offline_rows_after_reconcile():
    """Regression for the capacity-collapse scenario: once reconcile removes
    dead rows, the surviving count is below max and a backlog spawns again."""
    from app.services.docker_workers import PoolState, RunnerState, SpawnRunner, plan_scaling
    # After reconcile, only 1 live (saturated) runner remains; max is 3.
    live = RunnerState(runner_id="live", current_runs=2, max_concurrent_runs=2, online=True)
    actions = plan_scaling([PoolState(pool_id="p", queued=5, runners=[live], max_runners=3)])
    assert actions == [SpawnRunner("p")]
