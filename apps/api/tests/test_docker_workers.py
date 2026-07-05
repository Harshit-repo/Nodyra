"""Docker-worker autoscaling planner + spawn/remove/reconcile."""

from app.services.docker_workers import (
    PoolState,
    RunnerState,
    SpawnRunner,
    RemoveRunner,
    plan_scaling,
)


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


class FakeContainers:
    def __init__(self):
        self.run_calls = []
        self._by_name = {}

    def run(self, image, **kw):
        self.run_calls.append({"image": image, **kw})
        name = kw.get("name")
        c = type("C", (), {"name": name, "removed": False})()
        self._by_name[name] = c
        return c

    def get(self, name):
        if name not in self._by_name:
            raise KeyError(name)
        return self._by_name[name]

    def list(self, **kw):
        return list(self._by_name.values())


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
