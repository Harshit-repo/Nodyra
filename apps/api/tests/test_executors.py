"""RunExecutor seam (program A2): the executor protocol + adapters."""

import asyncio


def test_executor_protocol_shape():
    from app.services.executors.base import (
        RunExecutionContext,
        RunExecutor,
        RunOutcome,
    )

    ctx: RunExecutionContext = {
        "run_id": "r1",
        "workflow_id": "w1",
        "org_id": None,
        "graph": {"nodes": [], "edges": []},
        "cache": None,
        "targets": None,
        "environment_id": None,
        "runner_pool_id": None,
        "env_payload": None,
        "workflow_modules": [],
        "run_timeout": None,
        "default_timeouts": {},
        "pause_on_approval": True,
        "agent_action_resume": None,
        "subworkflow_meta": None,
    }
    assert ctx["run_id"] == "r1"
    assert RunOutcome(status="success").status == "success"
    assert hasattr(RunExecutor, "execute") and hasattr(RunExecutor, "cancel")


async def test_local_executor_delegates_to_pool_and_cancels_task():
    from app.services.executors.base import RunOutcome
    from app.services.executors.local import LocalExecutor

    calls: dict = {}

    class FakePool:
        async def dispatch(self, run_id, env_id, graph, cache, targets, on_event,
                           **kwargs):
            calls["dispatch"] = (run_id, env_id, kwargs.get("run_timeout"))
            return "success"

    active: dict[str, asyncio.Task] = {}
    ex = LocalExecutor(
        pool=FakePool(),
        subworkflow_resolver=lambda *a, **k: None,
        active_runs=active,
    )

    async def on_event(_event: dict) -> None: ...

    out = await ex.execute(
        {
            "run_id": "r1", "workflow_id": "w1",
            "graph": {"nodes": [], "edges": []},
            "cache": None, "targets": None,
            "environment_id": "env-9", "runner_pool_id": None,
            "env_payload": None, "workflow_modules": [],
            "run_timeout": 12.5, "default_timeouts": {},
            "pause_on_approval": True, "agent_action_resume": None,
        },
        on_event,
    )
    assert out == RunOutcome(status="success")
    assert calls["dispatch"] == ("r1", "env-9", 12.5)

    # cancel() cancels the registered task for the run id
    async def _hang():
        await asyncio.sleep(60)

    task = asyncio.ensure_future(_hang())
    active["r2"] = task
    assert await ex.cancel("r2") is True
    await asyncio.sleep(0)
    assert task.cancelled()
    assert await ex.cancel("missing") is False


async def test_remote_executor_delegates_to_dispatcher():
    from app.services.executors.base import RunOutcome
    from app.services.executors.remote import RemoteExecutor

    calls: dict = {}

    class FakeDispatcher:
        async def assign_run(self, run_id, pool_id, env_payload, graph, cache,
                             targets, workflow_modules, on_event,
                             pause_on_approval=False, agent_action_resume=None,
                             subworkflow_meta=None, sandbox_required=False):
            calls["assign"] = (run_id, pool_id, env_payload)
            calls["sandbox_required"] = sandbox_required
            return "success"

        async def cancel_remote_run(self, run_id, runner_id):
            calls["cancel"] = (run_id, runner_id)

    async def fake_runner_id(run_id):
        return "runner-7"

    ex = RemoteExecutor(dispatcher=FakeDispatcher(), runner_id_for=fake_runner_id)

    async def on_event(_event: dict) -> None: ...

    out = await ex.execute(
        {
            "run_id": "r1", "workflow_id": "w1",
            "graph": {"nodes": [], "edges": []},
            "cache": None, "targets": None,
            "environment_id": None, "runner_pool_id": "pool-1",
            "env_payload": {"id": "default"}, "workflow_modules": [],
            "run_timeout": None, "default_timeouts": {},
            "pause_on_approval": True, "agent_action_resume": None,
        },
        on_event,
    )
    assert out == RunOutcome(status="success")
    assert calls["assign"] == ("r1", "pool-1", {"id": "default"})

    assert await ex.cancel("r1") is True
    assert calls["cancel"] == ("r1", "runner-7")


def test_sandbox_executor_delegates_and_cancels():
    from app.services.executors.sandbox import SandboxExecutor

    calls = {}

    class FakeSandboxPool:
        enabled = True

        async def dispatch(self, run_id, **kw):
            calls["run_id"] = run_id
            calls["org_id"] = kw["org_id"]
            calls["env_id"] = kw["env_id"]
            calls["resolver"] = kw["subworkflow_resolver"]
            return "success"

        async def cancel(self, run_id):
            calls["cancelled"] = run_id
            return True

    async def resolver(call, parent_env_id=None):
        return None

    ex = SandboxExecutor(pool=FakeSandboxPool(), subworkflow_resolver=resolver)
    ctx = {
        "run_id": "r1", "workflow_id": "wf1", "org_id": "org9",
        "graph": {}, "cache": None, "targets": None,
        "environment_id": "env5", "runner_pool_id": None,
        "env_payload": {"id": "env5"}, "workflow_modules": [],
        "run_timeout": None, "default_timeouts": {},
        "pause_on_approval": False, "agent_action_resume": None,
        "subworkflow_meta": None,
    }

    async def on_event(e):
        pass

    outcome = asyncio.run(ex.execute(ctx, on_event))
    assert outcome.status == "success"
    assert calls["org_id"] == "org9" and calls["env_id"] == "env5"
    assert calls["resolver"] is resolver
    assert ex.active is True
    assert asyncio.run(ex.cancel("r1")) is True
    assert calls["cancelled"] == "r1"
