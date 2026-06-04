"""Engine integration: a tool-mode node is invoked by an AI Agent.

Mirrors the agent harness in test_engine_agent_actions.py — the agent node
returns an AgentActionRequest directly (no stub model needed), and we assert the
tool-mode node's real function runs with the AI-supplied argument.
"""

from __future__ import annotations

import asyncio
from typing import Any

from noodle.ai_runtime import (
    AgentActionRequest,
    AgentActionResponse,
    AIMessage,
    ToolAdapter,
    ToolCall,
)
from noodle.engine import execute
from noodle.models import Edge, GraphNode, RunStatus, WorkflowGraph
from noodle.node_tool import TOOL_MODE_OUTPUT
from noodle.sdk import NodeRegistry, node


def _registry() -> NodeRegistry:
    reg = NodeRegistry()

    @node(name="Echo Upper", id="echo_upper", registry=reg, params={"text": {}},
          tool_side_effecting=False)
    def echo_upper(text: str = "") -> dict:
        return {"shout": text.upper()}

    @node(
        name="Agent",
        id="agent",
        inputs=["tool"],
        input_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def agent(tool: ToolAdapter | None = None) -> AgentActionRequest:  # noqa: ARG001
        return AgentActionRequest(
            tool_calls=[
                ToolCall(id="call_1", name="shout", arguments={"text": "hello"})
            ],
            messages_so_far=[AIMessage.user("shout hello")],
            step=0,
            max_steps=3,
        )

    return reg


def test_tool_mode_node_is_invoked_by_agent() -> None:
    reg = _registry()
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="t",
                type="echo_upper",
                tool_mode=True,
                tool_name="shout",
                params={"text": "{{ $fromAI('text', 'what to shout', 'string') }}"},
            ),
            GraphNode(id="a", type="agent"),
        ],
        edges=[
            Edge(
                source="t",
                source_output=TOOL_MODE_OUTPUT,
                target="a",
                target_input="tool",
            )
        ],
    )
    result = asyncio.run(execute(graph, reg))

    assert result.status == RunStatus.success
    output = result.nodes["a"].outputs["main"]
    assert isinstance(output, AgentActionResponse)
    # The tool-mode node ran with the AI-supplied {"text": "hello"} → "HELLO".
    assert output.tool_results[0].content == '{"shout": "HELLO"}'
