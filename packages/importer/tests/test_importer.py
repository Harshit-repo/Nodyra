import pytest
import noodle_nodes  # noqa: F401 — registers built-ins
from noodle.models import WorkflowGraph
from noodle.sdk import registry
from noodle_exporter import workflow_to_module
from noodle_importer import import_module


def _export(graph_dict: dict) -> str:
    graph = WorkflowGraph.model_validate(graph_dict)
    return workflow_to_module(graph, "Test Workflow", registry=registry)


def test_roundtrip_two_node_pipeline():
    source = _export({
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}},
            {"id": "fetch", "type": "http_request",
             "params": {"url": "https://example.com", "method": "GET"},
             "position": {"x": 300, "y": 120}, "on_error": "continue"},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "fetch", "target_input": "input"}],
    })
    result = import_module(source)
    by_id = {n.id: n for n in result.nodes}
    assert by_id["t"].type == "manual_trigger"
    assert by_id["fetch"].type == "http_request"
    assert by_id["fetch"].params["url"] == "https://example.com"
    assert by_id["fetch"].on_error == "continue"
    assert any(e.source == "t" and e.target == "fetch" for e in result.edges)


def test_roundtrip_preserves_position():
    source = _export({
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}, "position": {"x": 10, "y": 20}},
        ],
        "edges": [],
    })
    result = import_module(source)
    node = result.nodes[0]
    assert node.position is not None
    assert node.position.x == 10
    assert node.position.y == 20


def test_extra_nodes_preserved():
    source = _export({
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}},
            {"id": "custom", "type": "user:abc123:my_fn", "params": {"x": 1}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "custom", "target_input": "input"}],
    })
    result = import_module(source)
    by_id = {n.id: n for n in result.nodes}
    assert "custom" in by_id
    assert by_id["custom"].type == "user:abc123:my_fn"


def test_invalid_source_raises_import_error():
    with pytest.raises(ImportError, match="No @node decorated functions"):
        import_module("x = 1\n")


def test_empty_graph_roundtrip():
    source = _export({
        "nodes": [{"id": "t", "type": "manual_trigger", "params": {}}],
        "edges": [],
    })
    result = import_module(source)
    assert len(result.nodes) == 1
    assert result.nodes[0].type == "manual_trigger"
