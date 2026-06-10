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
        sub_workflow_caller=lambda *a, **k: None,
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
