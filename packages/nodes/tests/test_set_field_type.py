"""Tests for the Set Field Type node."""
import pytest
import noodle_nodes  # noqa: F401
from noodle.engine import execute
from noodle.models import Edge, GraphNode, WorkflowGraph
from noodle.sdk import registry


def test_set_field_type_registered():
    ids = {m.id for m in registry.manifests()}
    assert "set_field_type" in ids


@pytest.mark.asyncio
async def test_set_field_type_converts_string_to_int():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": {"price": "42", "name": "widget"}},
            ),
            GraphNode(
                id="c",
                type="set_field_type",
                params={"field": "price", "to": "int"},
            ),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    out = result.nodes["c"].outputs["main"]
    assert out["price"] == 42
    assert isinstance(out["price"], int)
    assert out["name"] == "widget"  # untouched


@pytest.mark.asyncio
async def test_set_field_type_converts_string_to_float():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"score": "9.5"}}),
            GraphNode(id="c", type="set_field_type", params={"field": "score", "to": "float"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"]["score"] == 9.5


@pytest.mark.asyncio
async def test_set_field_type_converts_to_boolean():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"active": "true"}}),
            GraphNode(id="c", type="set_field_type", params={"field": "active", "to": "boolean"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"]["active"] is True


@pytest.mark.asyncio
async def test_set_field_type_converts_to_string():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"count": 7}}),
            GraphNode(id="c", type="set_field_type", params={"field": "count", "to": "string"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"]["count"] == "7"


@pytest.mark.asyncio
async def test_set_field_type_nested_dot_path():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": {"meta": {"count": "5"}, "name": "x"}},
            ),
            GraphNode(
                id="c",
                type="set_field_type",
                params={"field": "meta.count", "to": "int"},
            ),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    out = result.nodes["c"].outputs["main"]
    assert out["meta"]["count"] == 5
    assert out["name"] == "x"  # untouched


@pytest.mark.asyncio
async def test_set_field_type_nonexistent_field_passes_through():
    """A field path that doesn't exist is silently ignored — not an error."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"a": 1}}),
            GraphNode(id="c", type="set_field_type", params={"field": "missing", "to": "int"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"] == {"a": 1}


@pytest.mark.asyncio
async def test_set_field_type_non_dict_input_passes_through():
    """Non-dict inputs (e.g. a list) are returned unchanged."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": [1, 2, 3]}),
            GraphNode(id="c", type="set_field_type", params={"field": "x", "to": "string"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_set_field_type_empty_field_passes_through():
    """Blank field name means no conversion — pass through unchanged."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="t", type="manual_trigger", params={"data": {"x": "5"}}),
            GraphNode(id="c", type="set_field_type", params={"field": "", "to": "int"}),
        ],
        edges=[Edge(source="t", target="c")],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].outputs["main"] == {"x": "5"}
