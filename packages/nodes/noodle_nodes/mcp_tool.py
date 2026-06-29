"""MCP Tool node: execute a tool from a managed MCP connection.

Unlike the AI-v2 MCP nodes (which use the Credentials store), this node
resolves an org-scoped MCPConnection via RuntimeContext.call_mcp_tool(),
which the runner provides with DB access for connection loading and
secret decryption.
"""

from __future__ import annotations

from typing import Any

from noodle.engine.types import RuntimeContext
from noodle.sdk import node


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
    return await ctx.call_mcp_tool(conn_id, tool_name, resolved_args)
