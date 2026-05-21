import asyncio

import pytest

from noodle.engine import GraphError, execute
from noodle.models import Edge, GraphNode, NodeStatus, RunStatus, WorkflowGraph
from noodle.sdk import NodeRegistry, node


def make_registry() -> NodeRegistry:
    reg = NodeRegistry()

    @node(name="Const", id="const", inputs=[], registry=reg)
    def const(value: int = 0) -> int:
        return value

    @node(name="Double", id="double", registry=reg)
    def double(input: int = 0) -> int:
        return (input or 0) * 2

    @node(name="Add", id="add", inputs=["a", "b"], registry=reg)
    def add(a: int = 0, b: int = 0) -> int:
        return (a or 0) + (b or 0)

    @node(name="Gate", id="gate", outputs=["true", "false"], registry=reg)
    def gate(input: int = 0, flag: bool = False) -> dict:
        return {"true": input} if flag else {"false": input}

    @node(name="Boom", id="boom", registry=reg)
    def boom(input: int = 0) -> int:
        raise RuntimeError("kaboom")

    @node(name="AsyncDouble", id="adouble", registry=reg)
    async def adouble(input: int = 0) -> int:
        return (input or 0) * 2

    return reg


async def test_linear_graph_passes_values_along_edges() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c", type="const", params={"value": 5}),
            GraphNode(id="d", type="double"),
        ],
        edges=[Edge(source="c", target="d")],
    )
    result = await execute(graph, reg)
    assert result.status == RunStatus.success
    assert result.nodes["d"].outputs["main"] == 10


async def test_multiple_named_inputs() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c1", type="const", params={"value": 5}),
            GraphNode(id="c2", type="const", params={"value": 7}),
            GraphNode(id="sum", type="add"),
        ],
        edges=[
            Edge(source="c1", target="sum", target_input="a"),
            Edge(source="c2", target="sum", target_input="b"),
        ],
    )
    result = await execute(graph, reg)
    assert result.nodes["sum"].outputs["main"] == 12


async def test_async_node_is_awaited() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c", type="const", params={"value": 8}),
            GraphNode(id="d", type="adouble"),
        ],
        edges=[Edge(source="c", target="d")],
    )
    result = await execute(graph, reg)
    assert result.nodes["d"].outputs["main"] == 16


async def test_branching_skips_the_untaken_branch() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c", type="const", params={"value": 3}),
            GraphNode(id="g", type="gate", params={"flag": False}),
            GraphNode(id="t", type="double"),
            GraphNode(id="f", type="double"),
        ],
        edges=[
            Edge(source="c", target="g"),
            Edge(source="g", source_output="true", target="t"),
            Edge(source="g", source_output="false", target="f"),
        ],
    )
    result = await execute(graph, reg)
    assert result.nodes["t"].status == NodeStatus.skipped
    assert result.nodes["f"].status == NodeStatus.success
    assert result.nodes["f"].outputs["main"] == 6


async def test_partial_execution_uses_cached_outputs() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c", type="const", params={"value": 1}),
            GraphNode(id="d", type="double"),
        ],
        edges=[Edge(source="c", target="d")],
    )
    result = await execute(graph, reg, cache={"c": {"main": 50}})
    assert result.nodes["c"].outputs["main"] == 50
    assert result.nodes["d"].outputs["main"] == 100


async def test_targets_restrict_execution_to_a_subset() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c", type="const", params={"value": 4}),
            GraphNode(id="d", type="double"),
        ],
        edges=[Edge(source="c", target="d")],
    )
    result = await execute(graph, reg, targets=["c"])
    assert "c" in result.nodes
    assert "d" not in result.nodes


async def test_node_error_marks_run_failed_and_skips_downstream() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="b", type="boom"),
            GraphNode(id="d", type="double"),
        ],
        edges=[Edge(source="b", target="d")],
    )
    result = await execute(graph, reg)
    assert result.status == RunStatus.error
    assert result.nodes["b"].status == NodeStatus.error
    assert "kaboom" in result.nodes["b"].error
    assert result.nodes["d"].status == NodeStatus.skipped


async def test_unknown_node_type_errors() -> None:
    reg = make_registry()
    graph = WorkflowGraph(nodes=[GraphNode(id="x", type="does_not_exist")])
    result = await execute(graph, reg)
    assert result.status == RunStatus.error
    assert result.nodes["x"].status == NodeStatus.error


async def test_outputs_override_uses_dynamic_names() -> None:
    reg = NodeRegistry()

    @node(name="Dyn", id="dyn", outputs=["default"], registry=reg)
    def dyn(input: int = 0) -> dict:
        return {"alpha": 1, "beta": 2}

    graph = WorkflowGraph(
        nodes=[GraphNode(id="d", type="dyn", outputs_override=["alpha", "beta"])],
    )
    result = await execute(graph, reg)
    assert result.nodes["d"].outputs == {"alpha": 1, "beta": 2}


async def test_disabled_node_passes_input_through() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c", type="const", params={"value": 5}),
            GraphNode(id="d", type="double", disabled=True),
            GraphNode(id="e", type="double"),
        ],
        edges=[
            Edge(source="c", target="d"),
            Edge(source="d", target="e"),
        ],
    )
    result = await execute(graph, reg)
    # disabled node bypasses its function — input flows through to downstream.
    assert result.nodes["d"].outputs["main"] == 5
    assert result.nodes["e"].outputs["main"] == 10


async def test_expressions_resolve_against_upstream_data() -> None:
    reg = make_registry()

    @node(name="Echo", id="echo", registry=reg)
    def echo(input: int = 0, label: str = "") -> str:
        return f"{label}:{input}"

    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="c", type="const", params={"value": 7}),
            GraphNode(
                id="e",
                type="echo",
                params={"label": "n is {{ $json + 1 }}"},
            ),
        ],
        edges=[Edge(source="c", target="e")],
    )
    result = await execute(graph, reg)
    assert result.nodes["e"].outputs["main"] == "n is 8:7"


async def test_retry_on_fail_eventually_succeeds() -> None:
    reg = NodeRegistry()
    attempts: list[int] = [0]

    @node(name="Flaky", id="flaky", inputs=[], registry=reg)
    def flaky() -> int:
        attempts[0] += 1
        if attempts[0] < 3:
            raise RuntimeError("not yet")
        return attempts[0]

    graph = WorkflowGraph(
        nodes=[GraphNode(id="f", type="flaky", retry_on_fail=True, retries=3)],
    )
    result = await execute(graph, reg)
    assert result.status == RunStatus.success
    assert result.nodes["f"].outputs["main"] == 3
    assert attempts[0] == 3


async def test_continue_on_error_lets_downstream_run() -> None:
    reg = NodeRegistry()

    @node(name="Bang", id="bang", inputs=[], registry=reg)
    def bang() -> int:
        raise RuntimeError("nope")

    @node(name="Tail", id="tail", registry=reg)
    def tail(input: int | None = None) -> str:
        return f"got {input!r}"

    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="b", type="bang", on_error="continue"),
            GraphNode(id="t", type="tail"),
        ],
        edges=[Edge(source="b", target="t")],
    )
    result = await execute(graph, reg)
    # Failing node still errored, but the run continued and downstream ran
    # against a None passthrough.
    assert result.status == RunStatus.success
    assert result.nodes["b"].status == NodeStatus.error
    assert result.nodes["t"].outputs["main"] == "got None"


async def test_always_output_data_emits_a_value_on_failure() -> None:
    reg = NodeRegistry()

    @node(name="Bang", id="bang", inputs=[], registry=reg)
    def bang() -> int:
        raise RuntimeError("boom")

    graph = WorkflowGraph(
        nodes=[GraphNode(id="b", type="bang", always_output_data=True)],
    )
    result = await execute(graph, reg)
    assert result.nodes["b"].status == NodeStatus.error
    assert "main" in result.nodes["b"].outputs


async def test_node_logs_are_captured() -> None:
    reg = NodeRegistry()

    @node(name="Talker", id="talker", inputs=[], registry=reg)
    def talker() -> int:
        print("hello from node")
        return 1

    graph = WorkflowGraph(nodes=[GraphNode(id="t", type="talker")])
    result = await execute(graph, reg)
    assert any("hello from node" in line for line in result.nodes["t"].logs)


async def test_timeout_fails_a_slow_node() -> None:
    reg = NodeRegistry()

    @node(name="Slow", id="slow", inputs=[], registry=reg)
    async def slow() -> int:
        await asyncio.sleep(0.5)
        return 1

    graph = WorkflowGraph(
        nodes=[GraphNode(id="s", type="slow", timeout_seconds=0.05)],
    )
    result = await execute(graph, reg)
    assert result.status == RunStatus.error
    assert result.nodes["s"].status == NodeStatus.error
    assert "timed out" in result.nodes["s"].error


async def test_default_timeout_applies_to_code_nodes(monkeypatch) -> None:
    import time

    import noodle.engine as engine_module

    reg = NodeRegistry()
    monkeypatch.setitem(engine_module.DEFAULT_NODE_TIMEOUTS, "code", 0.01)

    @node(name="CodeLike", id="code", inputs=[], registry=reg)
    def slow_code() -> int:
        time.sleep(0.2)
        return 1

    result = await execute(WorkflowGraph(nodes=[GraphNode(id="c", type="code")]), reg)
    assert result.status == RunStatus.error
    assert result.nodes["c"].status == NodeStatus.error
    assert "timed out" in result.nodes["c"].error


async def test_node_timing_is_recorded() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[GraphNode(id="c", type="const", params={"value": 1})],
    )
    result = await execute(graph, reg)
    node = result.nodes["c"]
    assert node.started_at is not None
    assert node.finished_at is not None
    assert node.finished_at >= node.started_at


async def test_cycle_is_detected() -> None:
    reg = make_registry()
    graph = WorkflowGraph(
        nodes=[GraphNode(id="x", type="add"), GraphNode(id="y", type="add")],
        edges=[
            Edge(source="x", target="y", target_input="a"),
            Edge(source="y", target="x", target_input="a"),
        ],
    )
    with pytest.raises(GraphError):
        await execute(graph, reg)
