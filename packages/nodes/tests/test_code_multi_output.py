"""Tests for Code node multi-output via `output_<name>` variables."""

from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401
from noodle.engine import execute
from noodle.models import GraphNode, NodeStatus, WorkflowGraph
from noodle.sdk import registry
from noodle_nodes.builtin import (
    _collect_code_outputs,
    discover_code_output_ports,
)


def test_collect_code_outputs_single() -> None:
    is_multi, value = _collect_code_outputs({"output": 42})
    assert is_multi is False
    assert value == 42


def test_collect_code_outputs_multi_with_main() -> None:
    ns = {"output": 1, "output_clean": "c", "output_rejected": "r"}
    is_multi, value = _collect_code_outputs(ns)
    assert is_multi is True
    assert value == {"main": 1, "clean": "c", "rejected": "r"}


def test_collect_code_outputs_multi_without_main() -> None:
    ns = {"output_a": 1, "output_b": 2}
    is_multi, value = _collect_code_outputs(ns)
    assert is_multi is True
    assert value == {"a": 1, "b": 2}


def test_discover_code_output_ports_single() -> None:
    assert discover_code_output_ports("output = input") == ["main"]


def test_discover_code_output_ports_multi() -> None:
    code = "output = a\noutput_clean = b\noutput_rejected = c"
    assert discover_code_output_ports(code) == ["main", "clean", "rejected"]


def test_discover_code_output_ports_only_named() -> None:
    assert discover_code_output_ports("output_a = 1\noutput_b = 2") == ["a", "b"]


def test_discover_code_output_ports_invalid_syntax_falls_back() -> None:
    assert discover_code_output_ports("def foo(:") == ["main"]


@pytest.mark.asyncio
async def test_code_node_multi_output_routes_to_correct_port() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="c",
                type="code",
                params={
                    "code": (
                        "output = 1\n"
                        "output_double = 2\n"
                        "output_triple = 3\n"
                    )
                },
                outputs_override=["main", "double", "triple"],
            ),
        ],
    )
    result = await execute(graph, registry)
    assert result.nodes["c"].status == NodeStatus.success
    assert result.nodes["c"].outputs == {"main": 1, "double": 2, "triple": 3}
