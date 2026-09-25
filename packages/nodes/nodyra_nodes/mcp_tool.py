"""MCP Tool node: execute a tool from a managed MCP connection.

Unlike the AI-v2 MCP nodes (which use the Credentials store), this node
resolves an org-scoped MCPConnection via RuntimeContext.call_mcp_tool(),
which the runner provides with DB access for connection loading and
secret decryption.
"""

from __future__ import annotations

import json
import time
from typing import Any

from nodyra.context import node_debug
from nodyra.engine.types import RuntimeContext
from nodyra.expr import build_context, evaluate
from nodyra.sdk import node


def _preview(value: Any, limit: int = 2000) -> str:  # noqa: ANN401
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text[:limit]


def _with_mcp_trace(result: Any, trace: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN401
    output = dict(result) if isinstance(result, dict) else {"result": result}
    output["_mcp_trace"] = trace
    return output


def _record_debug_trace(
    debug: Any,
    trace: dict[str, Any],
    *,
    status: str,
    error: str | None = None,
) -> None:
    if not isinstance(debug, dict):
        return
    debug_trace = dict(trace)
    debug_trace["status"] = status
    if error:
        debug_trace["error"] = error
    debug.setdefault("mcp_calls", []).append(debug_trace)


@node(
    id="mcp_tool",
    name="MCP Tool",
    category="MCP",
    description="Execute a tool from an external MCP server",
    input_kinds={"main": "any"},
    output_kinds={"main": "any"},
    params={
        "connection_id": {
            "label": "MCP Connection",
            "required": True,
            "description": "ID of the managed MCP connection to call. The runner resolves it "
            "to its server URL and decrypted secrets.",
        },
        "tool_name": {
            "label": "Tool Name",
            "required": True,
            "description": "Name of the tool to invoke on the MCP server.",
        },
        "arguments": {
            "type": "object",
            "label": "Arguments",
            "description": "Tool arguments as a JSON object. {{ }} expressions are resolved "
            "against upstream node outputs.",
        },
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
    started = time.monotonic()
    try:
        result = await ctx.call_mcp_tool(conn_id, tool_name, resolved_args)
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        trace = {
            "connection_id": conn_id,
            "tool": tool_name,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "arguments_preview": _preview(resolved_args),
            "result_preview": _preview(error_text),
            "is_error": True,
        }
        _record_debug_trace(debug, trace, status="error", error=error_text)
        trace_json = json.dumps({"_mcp_trace": trace}, ensure_ascii=False, default=str)
        raise RuntimeError(f"{error_text}; {trace_json}") from exc

    trace = {
        "connection_id": conn_id,
        "tool": tool_name,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "arguments_preview": _preview(resolved_args),
        "result_preview": _preview(result),
        "is_error": False,
    }
    _record_debug_trace(debug, trace, status="success")
    return _with_mcp_trace(result, trace)
