"""RunExecutor seam (program A2): the executor protocol + adapters."""


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
