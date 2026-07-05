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
