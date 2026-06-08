from __future__ import annotations

import asyncio
from typing import Any

import pytest

from noodle.ai_runtime import (
    AgentActionRequest,
    AgentActionResponse,
    AgentResumeInput,
    AIMessage,
    ToolAdapter,
    ToolCall,
    ToolParameterSchema,
    ToolSchema,
)
from noodle.engine import execute
from noodle.models import Edge, GraphNode, NodeStatus, RunStatus, WorkflowGraph
from noodle.sdk import NodeRegistry, node


class EchoTool(ToolAdapter):
    def __init__(
        self,
        name: str = "echo",
        *,
        fail: bool = False,
        side_effecting: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._name = name
        self._fail = fail
        self._side_effecting = side_effecting

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="Echo arguments.",
            parameters=ToolParameterSchema(
                properties={"text": {"type": "string"}},
                required=["text"],
            ),
        )

    @property
    def side_effecting(self) -> bool:
        return self._side_effecting

    def invoke(self, arguments: dict[str, Any]) -> str:
        self.calls.append(arguments)
        if self._fail:
            raise RuntimeError("tool failed")
        return f"echo:{arguments.get('text')}"


class SlowTool(EchoTool):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        self.started.set()
        await asyncio.sleep(60)
        return self.invoke(arguments)


async def test_agent_action_request_dispatches_tool_and_returns_response() -> None:
    reg = NodeRegistry()
    tool = EchoTool()

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

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
                ToolCall(id="call_1", name="echo", arguments={"text": "hello"})
            ],
            messages_so_far=[AIMessage.user("say hello")],
            step=0,
            max_steps=3,
        )

    events: list[dict[str, Any]] = []
    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )
    result = await execute(graph, reg, on_event=lambda event: _collect(events, event))

    assert result.status == RunStatus.success
    output = result.nodes["a"].outputs["main"]
    assert isinstance(output, AgentActionResponse)
    assert output.tool_results[0].content == "echo:hello"
    assert tool.calls == [{"text": "hello"}]
    assert [e["type"] for e in events if e["type"].startswith("agent_")] == [
        "agent_action_requested",
        "agent_tool_started",
        "agent_tool_finished",
        "agent_action_completed",
    ]


async def test_agent_action_request_resumes_var_keyword_agent() -> None:
    reg = NodeRegistry()
    tool = EchoTool()

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

    @node(
        name="Agent",
        id="agent",
        inputs=["tool"],
        input_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def agent(tool: ToolAdapter | None = None, **runtime: Any) -> Any:  # noqa: ARG001
        resume = runtime.get("agent_resume")
        if isinstance(resume, AgentResumeInput):
            return {
                "step": resume.step,
                "results": [result.model_dump() for result in resume.tool_results],
            }
        return AgentActionRequest(
            tool_calls=[
                ToolCall(id="call_1", name="echo", arguments={"text": "resume"})
            ],
            messages_so_far=[AIMessage.user("go")],
            step=0,
            max_steps=3,
        )

    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )
    result = await execute(graph, reg)

    assert result.status == RunStatus.success
    output = result.nodes["a"].outputs["main"]
    assert output["step"] == 1
    assert output["results"][0]["content"] == "echo:resume"


async def test_agent_tool_failure_becomes_error_tool_result() -> None:
    reg = NodeRegistry()
    tool = EchoTool(fail=True)

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

    @node(
        name="Agent",
        id="agent",
        inputs=["tool"],
        input_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def agent(tool: ToolAdapter | None = None, **runtime: Any) -> Any:  # noqa: ARG001
        resume = runtime.get("agent_resume")
        if isinstance(resume, AgentResumeInput):
            return resume.tool_results[0].model_dump()
        return AgentActionRequest(
            tool_calls=[
                ToolCall(id="call_1", name="echo", arguments={"text": "fail"})
            ],
            messages_so_far=[AIMessage.user("go")],
            step=0,
            max_steps=3,
        )

    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )
    result = await execute(graph, reg)

    assert result.status == RunStatus.success
    output = result.nodes["a"].outputs["main"]
    assert output["is_error"] is True
    assert "tool failed" in output["content"]


async def test_agent_action_dispatches_multiple_tool_calls() -> None:
    reg = NodeRegistry()
    tool = EchoTool()

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

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
                ToolCall(id="call_1", name="echo", arguments={"text": "one"}),
                ToolCall(id="call_2", name="echo", arguments={"text": "two"}),
            ],
            messages_so_far=[AIMessage.user("go")],
            step=0,
            max_steps=3,
        )

    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )
    result = await execute(graph, reg)

    output = result.nodes["a"].outputs["main"]
    assert isinstance(output, AgentActionResponse)
    assert [r.content for r in output.tool_results] == ["echo:one", "echo:two"]
    assert tool.calls == [{"text": "one"}, {"text": "two"}]


async def test_agent_action_errors_when_max_steps_reached() -> None:
    reg = NodeRegistry()

    @node(name="Agent", id="agent", inputs=[], registry=reg)
    def agent() -> AgentActionRequest:
        return AgentActionRequest(
            tool_calls=[
                ToolCall(id="call_1", name="echo", arguments={"text": "stop"})
            ],
            messages_so_far=[AIMessage.user("go")],
            step=1,
            max_steps=1,
        )

    result = await execute(
        WorkflowGraph(nodes=[GraphNode(id="a", type="agent")]),
        reg,
    )

    assert result.status == RunStatus.error
    assert "max_steps=1" in (result.nodes["a"].error or "")


async def test_agent_side_effecting_tool_is_blocked_by_default() -> None:
    reg = NodeRegistry()
    tool = EchoTool(side_effecting=True)

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

    @node(
        name="Agent",
        id="agent",
        inputs=["tool"],
        input_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def agent(tool: ToolAdapter | None = None, **runtime: Any) -> Any:  # noqa: ARG001
        resume = runtime.get("agent_resume")
        if isinstance(resume, AgentResumeInput):
            return resume.tool_results[0].model_dump()
        return AgentActionRequest(
            tool_calls=[
                ToolCall(id="call_1", name="echo", arguments={"text": "write"})
            ],
            messages_so_far=[AIMessage.user("go")],
            step=0,
            max_steps=3,
        )

    events: list[dict[str, Any]] = []
    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )
    result = await execute(graph, reg, on_event=lambda event: _collect(events, event))

    output = result.nodes["a"].outputs["main"]
    assert output["is_error"] is True
    assert "requires approval" in output["content"]
    assert tool.calls == []
    assert "agent_tool_approval_required" in [event["type"] for event in events]


async def test_agent_side_effecting_tool_runs_when_allowed() -> None:
    reg = NodeRegistry()
    tool = EchoTool(side_effecting=True)

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

    @node(
        name="Agent",
        id="agent",
        inputs=["tool"],
        input_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def agent(tool: ToolAdapter | None = None, **runtime: Any) -> Any:  # noqa: ARG001
        resume = runtime.get("agent_resume")
        if isinstance(resume, AgentResumeInput):
            return resume.tool_results[0].model_dump()
        return AgentActionRequest(
            tool_calls=[
                ToolCall(id="call_1", name="echo", arguments={"text": "write"})
            ],
            messages_so_far=[AIMessage.user("go")],
            step=0,
            max_steps=3,
            allow_side_effects=True,
        )

    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )
    events: list[dict[str, Any]] = []
    result = await execute(graph, reg, on_event=lambda event: _collect(events, event))

    output = result.nodes["a"].outputs["main"]
    assert output["is_error"] is False
    assert output["content"] == "echo:write"
    assert tool.calls == [{"text": "write"}]
    assert "agent_tool_auto_approved" in [event["type"] for event in events]


async def test_agent_pauses_and_resumes_after_tool_approval() -> None:
    reg = NodeRegistry()
    tool = EchoTool(side_effecting=True)

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

    @node(
        name="Agent",
        id="agent",
        inputs=["tool"],
        input_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def agent(tool: ToolAdapter | None = None, **runtime: Any) -> Any:  # noqa: ARG001
        resume = runtime.get("agent_resume")
        if isinstance(resume, AgentResumeInput):
            return resume.tool_results[0].model_dump()
        return AgentActionRequest(
            tool_calls=[
                ToolCall(id="call_1", name="echo", arguments={"text": "write"})
            ],
            messages_so_far=[AIMessage.user("go")],
            step=0,
            max_steps=3,
        )

    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )

    waiting = await execute(graph, reg, pause_on_approval=True)
    waiting_node = waiting.nodes["a"]
    assert waiting.status == RunStatus.waiting
    assert waiting_node.status == NodeStatus.waiting
    state = waiting_node.debug["agent_approval_state"]
    request = AgentActionRequest.model_validate(state["request"])
    request.approved_tool_call_ids = ["call_1"]

    resumed = await execute(
        graph,
        reg,
        pause_on_approval=True,
        agent_action_resume={"a": request},
    )
    output = resumed.nodes["a"].outputs["main"]
    assert resumed.status == RunStatus.success
    assert output["is_error"] is False
    assert output["content"] == "echo:write"
    assert tool.calls == [{"text": "write"}]


async def test_agent_resumes_with_rejected_tool() -> None:
    reg = NodeRegistry()
    tool = EchoTool(side_effecting=True)

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

    @node(
        name="Agent",
        id="agent",
        inputs=["tool"],
        input_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def agent(tool: ToolAdapter | None = None, **runtime: Any) -> Any:  # noqa: ARG001
        resume = runtime.get("agent_resume")
        if isinstance(resume, AgentResumeInput):
            return resume.tool_results[0].model_dump()
        return AgentActionRequest(
            tool_calls=[
                ToolCall(id="call_1", name="echo", arguments={"text": "write"})
            ],
            messages_so_far=[AIMessage.user("go")],
            step=0,
            max_steps=3,
        )

    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )

    waiting = await execute(graph, reg, pause_on_approval=True)
    state = waiting.nodes["a"].debug["agent_approval_state"]
    request = AgentActionRequest.model_validate(state["request"])
    request.rejected_tool_call_ids = ["call_1"]

    resumed = await execute(
        graph,
        reg,
        pause_on_approval=True,
        agent_action_resume={"a": request},
    )
    output = resumed.nodes["a"].outputs["main"]
    # A denied call resolves the run (no longer waiting) and feeds an error
    # tool result back to the agent without invoking the side-effecting tool.
    assert resumed.status == RunStatus.success
    assert output["is_error"] is True
    assert "denied by the operator" in output["content"]
    assert tool.calls == []


async def test_agent_tool_dispatch_propagates_cancellation() -> None:
    reg = NodeRegistry()
    tool = SlowTool()

    @node(
        name="Tool",
        id="tool",
        inputs=[],
        outputs=["tool"],
        output_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def tool_node() -> ToolAdapter:
        return tool

    @node(
        name="Agent",
        id="agent",
        inputs=["tool"],
        input_kinds={"tool": "ai_tool"},
        registry=reg,
    )
    def agent(tool: ToolAdapter | None = None) -> AgentActionRequest:  # noqa: ARG001
        return AgentActionRequest(
            tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "wait"})],
            messages_so_far=[AIMessage.user("go")],
            step=0,
            max_steps=3,
        )

    graph = WorkflowGraph(
        nodes=[GraphNode(id="t", type="tool"), GraphNode(id="a", type="agent")],
        edges=[
            Edge(source="t", source_output="tool", target="a", target_input="tool")
        ],
    )

    task = asyncio.create_task(execute(graph, reg))
    await asyncio.wait_for(tool.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def _collect(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    events.append(event)
