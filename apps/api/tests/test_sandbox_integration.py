"""Real-daemon sandbox integration. Skipped unless NOODLE_SANDBOX_IT=1.

Run manually on a machine with Docker:
    NOODLE_SANDBOX_IT=1 python -m pytest tests/test_sandbox_integration.py -v
Builds a real noodle-env image on first run (slow); subsequent runs reuse it.
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("NOODLE_SANDBOX_IT") != "1",
    reason="set NOODLE_SANDBOX_IT=1 to run sandbox integration tests",
)

# Same minimal passing graph as tests/test_runs.py.
GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {"data": {"n": 3}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "output = input['n'] * 2"},
            "position": {"x": 250, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "input",
        }
    ],
}

ENV_PAYLOAD = {
    "id": "default",
    "packages_hash": "it",
    "python_version": "3.12",
    "packages": [],
}


def test_run_executes_in_real_container(monkeypatch):
    from app.config import settings
    from app.services import sandbox_pool as sp

    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    fresh = sp.SandboxPool()
    monkeypatch.setattr(sp, "pool", fresh)

    async def scenario():
        runtime = await sp.init_sandbox()
        assert runtime is not None, "no Docker daemon — cannot run integration test"
        events = []

        async def on_event(e):
            events.append(e)

        status = await fresh.dispatch(
            "it-run-1", org_id="it-org", env_id=None,
            env_payload=ENV_PAYLOAD, graph=GRAPH, cache=None, targets=None,
            workflow_modules=[], on_event=on_event,
        )
        # Warm reuse: same (org, env) key — must reuse the idle container.
        status2 = await fresh.dispatch(
            "it-run-2", org_id="it-org", env_id=None,
            env_payload=ENV_PAYLOAD, graph=GRAPH, cache=None, targets=None,
            workflow_modules=[], on_event=on_event,
        )
        await fresh.flush()
        return status, status2, events

    status, status2, events = asyncio.run(scenario())
    assert status == "success", f"events: {events}"
    assert status2 == "success"
    assert any(e["type"] == "node_finished" for e in events)
