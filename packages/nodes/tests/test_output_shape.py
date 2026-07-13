from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import nodyra_nodes  # noqa: F401 - importing registers the built-in nodes
from nodyra.artifacts import LocalArtifactStore
from nodyra.context import artifact_store, current_node_id
from nodyra.datasets import is_dataset_ref
from nodyra.engine import execute
from nodyra.models import Edge, GraphNode, NodeStatus, WorkflowGraph
from nodyra.sdk import registry


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="output-shape")
    artifact_token = artifact_store.set(store)
    node_token = current_node_id.set("output-shape-node")
    yield store
    current_node_id.reset(node_token)
    artifact_store.reset(artifact_token)


def _assert_output_ports(result: Any, expected_ports: dict[str, set[str]]) -> None:
    for node_id, ports in expected_ports.items():
        node_result = result.nodes[node_id]
        assert node_result.status == NodeStatus.success, node_result.error
        assert isinstance(node_result.outputs, Mapping)
        assert set(node_result.outputs) == ports


async def test_output_shape_contract_for_main_port_nodes() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={
                    "data": [
                        {"name": "beta", "score": 2},
                        {"name": "alpha", "score": 1},
                    ],
                },
            ),
            GraphNode(
                id="edit",
                type="edit_fields",
                params={"fields": {"active": True}},
            ),
            GraphNode(
                id="sort",
                type="sort",
                params={"field": "name", "order": "ascending"},
            ),
            GraphNode(id="limit", type="limit", params={"max_items": 1}),
        ],
        edges=[
            Edge(source="t", target="edit"),
            Edge(source="edit", target="sort"),
            Edge(source="sort", target="limit"),
        ],
    )

    result = await execute(graph, registry)

    _assert_output_ports(
        result,
        {
            "t": {"main"},
            "edit": {"main"},
            "sort": {"main"},
            "limit": {"main"},
        },
    )
    assert result.nodes["limit"].outputs["main"] == [
        {"name": "alpha", "score": 1, "active": True}
    ]


async def test_output_shape_contract_for_branching_nodes() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": {"kind": "vip", "status": "open"}},
            ),
            GraphNode(
                id="i",
                type="if",
                params={"field": "status", "operator": "equals", "value": "open"},
            ),
            GraphNode(
                id="s",
                type="switch",
                params={"field": "kind", "rules": {"vip": "vip"}},
                outputs_override=["vip", "fallback"],
            ),
            GraphNode(
                id="items",
                type="manual_trigger",
                params={"data": [{"id": 1}, {"id": 2}]},
            ),
            GraphNode(id="loop", type="loop_over_items"),
            GraphNode(id="kept", type="no_op"),
            GraphNode(id="caught", type="no_op"),
            GraphNode(id="each", type="no_op"),
            GraphNode(id="done", type="no_op"),
        ],
        edges=[
            Edge(source="t", target="i"),
            Edge(source="i", source_output="true", target="kept"),
            Edge(source="t", target="s"),
            Edge(source="s", source_output="vip", target="caught"),
            Edge(source="items", target="loop"),
            Edge(source="loop", source_output="item", target="each"),
            Edge(source="loop", source_output="done", target="done"),
        ],
    )

    result = await execute(graph, registry)

    _assert_output_ports(
        result,
        {
            "t": {"main"},
            "i": {"true"},
            "s": {"vip"},
            "items": {"main"},
            "loop": {"item", "done"},
            "kept": {"main"},
            "caught": {"main"},
            "each": {"main"},
            "done": {"main"},
        },
    )
    assert result.nodes["each"].outputs["main"] == [{"id": 1}, {"id": 2}]
    assert result.nodes["done"].outputs["main"] == {
        "items": [{"id": 1}, {"id": 2}],
        "count": 2,
    }


async def test_output_shape_contract_for_dataset_nodes(store_ctx) -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="manual_trigger",
                params={"data": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]},
            ),
            GraphNode(id="ds", type="records_to_dataset"),
            GraphNode(id="rows", type="dataset_to_records", params={"max_rows": 10}),
        ],
        edges=[
            Edge(source="t", target="ds"),
            Edge(source="ds", target="rows"),
        ],
    )

    result = await execute(graph, registry)

    _assert_output_ports(
        result,
        {
            "t": {"main"},
            "ds": {"main"},
            "rows": {"main"},
        },
    )
    assert is_dataset_ref(result.nodes["ds"].outputs["main"])
    assert result.nodes["rows"].outputs["main"] == [
        {"id": 1, "name": "Ada"},
        {"id": 2, "name": "Grace"},
    ]
