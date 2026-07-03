"""Regression tests for the ``mcp_tool`` node.

Commit aa575191 dropped the ``noodle.expr`` import while reformatting the
node's params, so every execution raised ``NameError: name 'build_context'
is not defined``. These tests execute the node function directly (no
manifest-only coverage) so a missing import can never ship silently again.
"""

import pytest

from noodle.context import node_debug
from noodle.engine.types import RuntimeContext, set_call_mcp_tool_impl
from noodle_nodes.mcp_tool import mcp_tool


@pytest.fixture
def recorded_calls():
    calls: list[tuple[str, str, dict]] = []

    async def fake_call(connection_id: str, tool_name: str, arguments: dict):
        calls.append((connection_id, tool_name, arguments))
        return {"ok": True}

    set_call_mcp_tool_impl(fake_call)
    return calls


async def test_mcp_tool_executes_and_dispatches(recorded_calls):
    ctx = RuntimeContext(
        run_id="r1",
        workflow_id="w1",
        node_params={
            "connection_id": "conn-1",
            "tool_name": "echo",
            "arguments": {"message": "hello"},
        },
        node_inputs={"main": {"value": 42}},
    )
    result = await mcp_tool(input={"value": 42}, ctx=ctx)
    assert result == {"ok": True}
    assert recorded_calls == [("conn-1", "echo", {"message": "hello"})]


async def test_mcp_tool_resolves_expressions_from_input(recorded_calls):
    ctx = RuntimeContext(
        run_id="r1",
        workflow_id="w1",
        node_params={
            "connection_id": "conn-1",
            "tool_name": "echo",
            "arguments": {"message": "{{ $json.city }}"},
        },
        node_inputs={"main": {"city": "Sydney"}},
    )
    await mcp_tool(input={"city": "Sydney"}, ctx=ctx)
    assert recorded_calls[0][2] == {"message": "Sydney"}


async def test_mcp_tool_defaults_empty_arguments(recorded_calls):
    ctx = RuntimeContext(
        run_id="r1",
        workflow_id="w1",
        node_params={"connection_id": "conn-1", "tool_name": "ping"},
        node_inputs={},
    )
    await mcp_tool(input=None, ctx=ctx)
    assert recorded_calls == [("conn-1", "ping", {})]


async def test_mcp_tool_records_call_trace(recorded_calls):
    debug: dict = {}
    token = node_debug.set(debug)
    try:
        ctx = RuntimeContext(
            run_id="r1",
            workflow_id="w1",
            node_params={
                "connection_id": "conn-1",
                "tool_name": "echo",
                "arguments": {"a": 1},
            },
            node_inputs={},
        )
        await mcp_tool(input=None, ctx=ctx)
    finally:
        node_debug.reset(token)
    (call,) = debug["mcp_calls"]
    assert call["tool"] == "echo"
    assert call["connection_id"] == "conn-1"
    assert call["status"] == "success"
    assert isinstance(call["duration_ms"], int)


async def test_mcp_tool_records_failed_call(recorded_calls):
    async def boom(connection_id, tool_name, arguments):
        raise RuntimeError("server unreachable")

    set_call_mcp_tool_impl(boom)
    debug: dict = {}
    token = node_debug.set(debug)
    try:
        ctx = RuntimeContext(
            run_id="r1",
            workflow_id="w1",
            node_params={"connection_id": "conn-1", "tool_name": "echo"},
            node_inputs={},
        )
        with pytest.raises(RuntimeError):
            await mcp_tool(input=None, ctx=ctx)
    finally:
        node_debug.reset(token)
    assert debug["mcp_calls"][0]["status"] == "error"
