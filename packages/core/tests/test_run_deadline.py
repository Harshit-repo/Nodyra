"""A run deadline bounds a node that is already executing."""

import time

import pytest

from noodle.context import cancel_event
from noodle.engine import execute
from noodle.models import GraphNode, WorkflowGraph
from noodle.sdk import NodeRegistry, node


@pytest.fixture
def registry():
    reg = NodeRegistry()

    @node(id="sleepy", name="Sleepy", category="test", registry=reg)
    def sleepy(input=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if cancel_event.get().is_set():
                return {"main": {"cancelled": True}}
            time.sleep(0.05)
        return {"main": {"ok": True}}

    return reg


async def test_run_deadline_bounds_running_node(registry):
    graph = WorkflowGraph(
        nodes=[GraphNode(id="n1", type="sleepy", params={})],
        edges=[],
    )
    start = time.monotonic()
    result = await execute(graph, registry, run_timeout_seconds=1.0)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0
    assert str(result.status) == "error"
    assert "timed out" in (result.nodes["n1"].error or "")
