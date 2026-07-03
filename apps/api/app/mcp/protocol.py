"""MCP server protocol layer: JSON-RPC 2.0 + MCP envelope helpers.

Implements the MCP streamable-HTTP transport in *stateless JSON mode*: each
request is a single JSON-RPC message POSTed to ``/mcp`` and answered with a
single ``application/json`` response. The MCP spec permits this — a server
MAY return JSON instead of an SSE stream and MAY operate without sessions.
No server-initiated streams, no resumability, no ``Mcp-Session-Id``.
"""

import json
from typing import Any

PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SERVER_INFO = {"name": "nodyra", "version": "0.0.1"}
SERVER_CAPABILITIES: dict[str, Any] = {
    # This endpoint is intentionally stateless and opens no server-initiated
    # stream, so it cannot truthfully emit tools/list_changed notifications.
    "tools": {"listChanged": False},
    "resources": {"subscribe": False, "listChanged": False},
    "prompts": {"listChanged": False},
}


def jsonrpc_result(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def jsonrpc_error(
    req_id: Any, code: int, message: str, data: Any = None
) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def initialize_result(client_protocol_version: Any) -> dict:
    version = (
        client_protocol_version
        if client_protocol_version in SUPPORTED_PROTOCOL_VERSIONS
        else PROTOCOL_VERSION
    )
    return {
        "protocolVersion": version,
        "capabilities": SERVER_CAPABILITIES,
        "serverInfo": SERVER_INFO,
    }


def tool_result(payload: Any, *, is_error: bool = False) -> dict:
    """Wrap a tool handler's return value as an MCP ``tools/call`` result."""
    if isinstance(payload, str):
        text = payload
        structured = None
    else:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        structured = payload if isinstance(payload, dict) else None
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": bool(is_error),
    }
    if structured is not None and not is_error:
        result["structuredContent"] = structured
    return result
