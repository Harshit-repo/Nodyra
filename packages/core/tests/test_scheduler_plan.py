"""_build_plan: dependency graph over scheduling units, with loop regions
contracted into their loop_start node."""

from nodyra.engine import _loop_regions
from nodyra.engine.scheduler import _build_plan
from nodyra.models import Edge, GraphNode, WorkflowGraph


def _loop_graph() -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(id="pre", type="const", params={"value": [1, 2]}),
            GraphNode(id="ls", type="loop_start"),
            GraphNode(id="body", type="double"),
            GraphNode(id="le", type="loop_end", params={"loop_start_id": "ls"}),
            GraphNode(id="after", type="double"),
        ],
        edges=[
            Edge(source="pre", target="ls", target_input="input"),
            Edge(source="ls", target="body", source_output="item"),
            Edge(source="body", target="le", target_input="input"),
            Edge(source="le", target="after", source_output="results"),
        ],
    )


def test_plan_contracts_loop_region_into_start_unit():
    graph = _loop_graph()
    regions = _loop_regions(graph)
    owned = set(regions["ls"].body_ids) | {regions["ls"].end_id}
    node_ids = {n.id for n in graph.nodes}

    plan = _build_plan(graph, node_ids, owned, regions)

    assert plan.units == ["pre", "ls", "after"]  # insertion order, owned excluded
    assert plan.deps["after"] == {"ls"}  # le is owned → dep maps to the ls unit
    assert plan.deps["ls"] == {"pre"}
    assert plan.deps["pre"] == set()


def test_plan_for_loop_body_units():
    graph = _loop_graph()
    regions = _loop_regions(graph)
    body_plan = _build_plan(graph, set(regions["ls"].body_ids), set(), regions)

    # ls is outside the body set: the body node has no in-set deps and is
    # immediately ready (its input comes from iter_outputs).
    assert body_plan.units == ["body"]
    assert body_plan.deps["body"] == set()


def test_plan_drops_deps_on_nodes_outside_the_executed_set():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="a", type="const", params={"value": 1}),
            GraphNode(id="b", type="double"),
        ],
        edges=[Edge(source="a", target="b")],
    )
    # Targeted run where 'a' is cached out of the set: b must not deadlock.
    plan = _build_plan(graph, {"b"}, set(), {})
    assert plan.units == ["b"]
    assert plan.deps["b"] == set()
