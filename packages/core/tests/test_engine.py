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


async def test_matching_ai_port_kinds_are_allowed() -> None:
    reg = NodeRegistry()

    @node(
        name="Model",
        id="model",
        inputs=[],
        outputs=["model"],
        output_kinds={"model": "ai_language_model"},
        registry=reg,
    )
    def model() -> dict:
        return {"provider": "test"}

    @node(
        name="Agent",
        id="agent",
        inputs=["model"],
        input_kinds={"model": "ai_language_model"},
        registry=reg,
    )
    def agent(model=None) -> str:
        return str(model["provider"])

    graph = WorkflowGraph(
        nodes=[GraphNode(id="m", type="model"), GraphNode(id="a", type="agent")],
        edges=[Edge(source="m", source_output="model", target="a", target_input="model")],
    )

    result = await execute(graph, reg)

    assert result.status == RunStatus.success
    assert result.nodes["a"].outputs["main"] == "test"


async def test_ai_port_kind_mismatch_is_rejected_before_execution() -> None:
    reg = NodeRegistry()

    @node(
        name="Records",
        id="records",
        inputs=[],
        output_kinds={"main": "main"},
        registry=reg,
    )
    def records() -> dict:
        return {"rows": []}

    @node(
        name="Agent",
        id="agent",
        inputs=["model"],
        input_kinds={"model": "ai_language_model"},
        registry=reg,
    )
    def agent(model=None) -> str:  # pragma: no cover - graph validation should stop first
        return str(model)

    graph = WorkflowGraph(
        nodes=[GraphNode(id="r", type="records"), GraphNode(id="a", type="agent")],
        edges=[Edge(source="r", target="a", target_input="model")],
    )

    with pytest.raises(GraphError, match="AI language model"):
        await execute(graph, reg)


async def test_port_kind_mismatch_outside_targets_is_ignored() -> None:
    reg = NodeRegistry()

    @node(
        name="Records",
        id="records",
        inputs=[],
        output_kinds={"main": "main"},
        registry=reg,
    )
    def records() -> dict:
        return {"rows": []}

    @node(
        name="Agent",
        id="agent",
        inputs=["model"],
        input_kinds={"model": "ai_language_model"},
        registry=reg,
    )
    def agent(model=None) -> str:
        return str(model)

    @node(name="Const", id="const", inputs=[], registry=reg)
    def const() -> int:
        return 1

    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="r", type="records"),
            GraphNode(id="a", type="agent"),
            GraphNode(id="c", type="const"),
        ],
        edges=[Edge(source="r", target="a", target_input="model")],
    )

    result = await execute(graph, reg, targets=["c"])

    assert result.status == RunStatus.success
    assert set(result.nodes) == {"c"}


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


async def test_disabled_ai_supplier_errors_instead_of_silent_none() -> None:
    reg = NodeRegistry()

    @node(
        name="Memory",
        id="memory",
        inputs=[],
        outputs=["memory"],
        output_kinds={"memory": "ai_memory"},
        registry=reg,
    )
    def memory() -> object:
        return object()

    @node(
        name="Agent",
        id="agent",
        inputs=["memory"],
        input_kinds={"memory": "ai_memory"},
        registry=reg,
    )
    def agent(memory: object | None = None) -> str:  # noqa: ARG001
        return "ok"

    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="m", type="memory", disabled=True),
            GraphNode(id="a", type="agent"),
        ],
        edges=[
            Edge(
                source="m",
                source_output="memory",
                target="a",
                target_input="memory",
            )
        ],
    )
    result = await execute(graph, reg)

    assert result.status == RunStatus.error
    assert result.nodes["a"].status == NodeStatus.error
    assert "expected AI memory" in (result.nodes["a"].error or "")
    assert "Enable the upstream node" in (result.nodes["a"].error or "")


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


async def test_branch_order_depends_on_insertion_not_position() -> None:
    """Shuffling node x/y positions must not change execution order.

    Two independent branches fan out from a common source. The branch that
    appears first in ``graph.nodes`` must run first regardless of canvas
    coordinates or lexical id order.
    """
    from noodle.engine import _topo_order
    from noodle.models import Position

    def build(positions: dict[str, tuple[float, float]]) -> WorkflowGraph:
        return WorkflowGraph(
            nodes=[
                GraphNode(
                    id="src", type="const", params={"value": 1},
                    position=Position(x=positions["src"][0], y=positions["src"][1]),
                ),
                # "z_first" is inserted before "a_second" but sorts after by id.
                GraphNode(
                    id="z_first", type="double",
                    position=Position(x=positions["z_first"][0], y=positions["z_first"][1]),
                ),
                GraphNode(
                    id="a_second", type="double",
                    position=Position(x=positions["a_second"][0], y=positions["a_second"][1]),
                ),
            ],
            edges=[
                Edge(source="src", target="z_first"),
                Edge(source="src", target="a_second"),
            ],
        )

    layout_a = {"src": (0, 0), "z_first": (100, 0), "a_second": (200, 0)}
    layout_b = {"src": (999, 999), "z_first": (-50, -50), "a_second": (-999, 999)}

    order_a = _topo_order(build(layout_a))
    order_b = _topo_order(build(layout_b))

    # Insertion order wins; lexical id sort would have placed "a_second" before "z_first".
    assert order_a == ["src", "z_first", "a_second"]
    assert order_a == order_b


# ---------------------------------------------------------------------------
# Per-key process pool isolation
# ---------------------------------------------------------------------------


def test_process_pool_returns_same_pool_for_same_key() -> None:
    """The same key always returns the same pool object (reuse)."""
    import noodle.engine as _eng
    pool_a1 = _eng._get_process_pool(key="env-alpha")
    pool_a2 = _eng._get_process_pool(key="env-alpha")
    assert pool_a1 is pool_a2


def test_process_pool_returns_different_pools_for_different_keys() -> None:
    """Different keys get different pool objects (isolation)."""
    import noodle.engine as _eng
    pool_a = _eng._get_process_pool(key="env-x")
    pool_b = _eng._get_process_pool(key="env-y")
    assert pool_a is not pool_b


def test_process_pool_none_key_is_its_own_pool() -> None:
    """key=None (default/no env) has its own pool, distinct from named envs."""
    import noodle.engine as _eng
    pool_none = _eng._get_process_pool(key=None)
    pool_named = _eng._get_process_pool(key="env-z")
    assert pool_none is not pool_named


def test_worse_status_ranking() -> None:
    """error outranks waiting outranks success, regardless of argument order."""
    from noodle.engine import _worse_status

    assert _worse_status(RunStatus.success, RunStatus.waiting) is RunStatus.waiting
    assert _worse_status(RunStatus.waiting, RunStatus.success) is RunStatus.waiting
    assert _worse_status(RunStatus.waiting, RunStatus.error) is RunStatus.error
    assert _worse_status(RunStatus.error, RunStatus.waiting) is RunStatus.error
    assert _worse_status(RunStatus.error, RunStatus.success) is RunStatus.error
    assert _worse_status(RunStatus.success, RunStatus.success) is RunStatus.success


async def test_independent_branches_are_not_level_barriered() -> None:
    """src→slow→c and src→fast→d: d must finish before c starts.

    Under level barriers c and d share a level, so d waits for slow (0.4s)
    even though its own parent finished at 0.05s. Dependency counting starts
    d as soon as fast completes."""
    reg = NodeRegistry()

    @node(name="One", id="one", inputs=[], registry=reg)
    def one() -> int:
        return 1

    @node(name="SlowEcho", id="slow_echo", registry=reg)
    async def slow_echo(input: int = 0) -> int:
        await asyncio.sleep(0.4)
        return input

    @node(name="FastEcho", id="fast_echo", registry=reg)
    async def fast_echo(input: int = 0) -> int:
        await asyncio.sleep(0.05)
        return input

    @node(name="Echo", id="echo", registry=reg)
    def echo(input: int = 0) -> int:
        return input

    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="src", type="one"),
            GraphNode(id="slow", type="slow_echo"),
            GraphNode(id="fast", type="fast_echo"),
            GraphNode(id="c", type="echo"),
            GraphNode(id="d", type="echo"),
        ],
        edges=[
            Edge(source="src", target="slow"),
            Edge(source="src", target="fast"),
            Edge(source="slow", target="c"),
            Edge(source="fast", target="d"),
        ],
    )
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    result = await execute(graph, reg, on_event=on_event)
    assert result.status == RunStatus.success
    d_finished = next(
        i for i, e in enumerate(events)
        if e["type"] == "node_finished" and e["node_id"] == "d"
    )
    c_started = next(
        i for i, e in enumerate(events)
        if e["type"] == "node_started" and e["node_id"] == "c"
    )
    assert d_finished < c_started, "d should complete before the slow branch unblocks c"
