"""B5: the node-output size cap must not pay a full JSON encode for outputs
that are obviously small. The bound is conservative: it may overestimate
(falling back to a real measure) but must never underestimate."""

import json

import pytest

from nodyra.engine import execute, node_exec
from nodyra.engine.node_exec import _encoded_upper_bound
from nodyra.models import GraphNode, NodeStatus, RunStatus, WorkflowGraph
from nodyra.sdk import NodeRegistry, node


@pytest.mark.parametrize(
    "value",
    [
        None, True, False, 0, 42, -7, 3.14, 1e300,
        "hello", "",
        [1, 2, 3], {"a": 1, "b": "x"},
        {"main": [1.5, None, "ok"]},
    ],
)
def test_bound_never_underestimates(value):
    bound = _encoded_upper_bound(value)
    assert bound is not None
    assert bound >= len(json.dumps(value, default=str))


@pytest.mark.parametrize(
    "value",
    [
        10**40,                          # huge int — no cheap bound
        list(range(100)),                # over the 64-element scan cap
        {"a": {"b": {"c": 1}}},          # deeper than the 2-level scan
        {1: "non-string-key"},
        object(),
    ],
)
def test_unbounded_shapes_fall_back_to_full_measure(value):
    assert _encoded_upper_bound(value) is None


async def test_small_outputs_skip_the_full_encode(monkeypatch):
    calls = {"n": 0}
    real = node_exec._approx_encoded_length

    def counting(value):
        calls["n"] += 1
        return real(value)

    monkeypatch.setattr(node_exec, "_approx_encoded_length", counting)

    reg = NodeRegistry()

    @node(name="Small", id="small", inputs=[], registry=reg)
    def small() -> dict:
        return {"value": 7, "label": "ok"}

    graph = WorkflowGraph(nodes=[GraphNode(id="s", type="small")])
    result = await execute(graph, reg, max_node_output_bytes=10_000)
    assert result.status == RunStatus.success
    assert calls["n"] == 0, "small output paid a full JSON encode"


async def test_oversized_output_still_errors():
    reg = NodeRegistry()

    @node(name="Big", id="big", inputs=[], registry=reg)
    def big() -> str:
        return "x" * 5000

    graph = WorkflowGraph(nodes=[GraphNode(id="b", type="big")])
    result = await execute(graph, reg, max_node_output_bytes=100)
    assert result.nodes["b"].status == NodeStatus.error
    assert "exceeds limit" in result.nodes["b"].error
