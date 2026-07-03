"""MCP Tool node: execute a tool from a managed MCP connection.

Unlike the AI-v2 MCP nodes (which use the Credentials store), this node
resolves an org-scoped MCPConnection via RuntimeContext.call_mcp_tool(),
which the runner provides with DB access for connection loading and
secret decryption.
"""

from __future__ import annotations

import time
from typing import Any

from nodyra.context import node_debug
from nodyra.engine.types import RuntimeContext
from nodyra.expr import build_context, evaluate
from nodyra.sdk import node


@node(
    id="mcp_tool",
    name="MCP Tool",
    category="MCP",
    description="Execute a tool from an external MCP server",
    input_kinds={"main": "any"},
    output_kinds={"main": "any"},
    params={
        "connection_id": {"type": "mcp_connection", "label": "MCP Connection", "required": True},
        "tool_name": {"type": "string", "label": "Tool Name", "required": True},
        "arguments": {"type": "object", "label": "Arguments"},
    },
)
async def mcp_tool(input: Any = None, *, ctx: RuntimeContext) -> Any:  # noqa: ANN401
    """Execute an MCP tool call through the runner's platform hook."""
    params = ctx.node_params
    conn_id = params.get("connection_id", "")
    tool_name = params.get("tool_name", "")
    arguments = params.get("arguments") or {}

    # Resolve {{ }} expression refs from upstream node outputs.
    expr_context = build_context(
        first_input=ctx.node_inputs.get("main", {}),
        inputs=ctx.node_inputs,
        node_outputs={},
    )
    resolved_args = evaluate(arguments, expr_context)

    # Dispatch through the platform hook (implemented in runner's RuntimeContext).
    debug = node_debug.get()
    trace: dict[str, Any] = {"connection_id": conn_id, "tool": tool_name}
    started = time.monotonic()
    try:
        result = await ctx.call_mcp_tool(conn_id, tool_name, resolved_args)
    except Exception as exc:
        trace["status"] = "error"
        trace["error"] = f"{type(exc).__name__}: {exc}"
        raise
    else:
        trace["status"] = "success"
        return result
    finally:
        trace["duration_ms"] = int((time.monotonic() - started) * 1000)
        if isinstance(debug, dict):
            debug.setdefault("mcp_calls", []).append(trace)
