"""MCP client: connects to external MCP servers, discovers tools, executes calls."""

from __future__ import annotations

import logging
from itertools import count
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MCPConnection
from app.services.org_keys import get_org_kek

_logger = logging.getLogger(__name__)

_JSON_TYPE_TO_PORT_KIND: dict[str, str] = {
    "string": "string",
    "number": "number",
    "integer": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}

# Headers the caller is never allowed to override via conn.headers
_DANGEROUS_HEADERS = frozenset({
    "connection", "transfer-encoding", "host", "content-length",
    "cookie", "authorization", "set-cookie",
})


def _is_dangerous_header(name: str) -> bool:
    """Return True if *name* (lowercase) matches the dangerous header blocklist."""
    lower = name.lower()
    if lower in _DANGEROUS_HEADERS:
        return True
    if lower.startswith("x-forwarded-") or lower.startswith("proxy-"):
        return True
    return False


class MCPError(Exception):
    """Raised when an MCP server returns a JSON-RPC error response."""


def _build_auth_headers(
    conn: MCPConnection, decrypted_secret: str | None
) -> dict[str, str]:
    """Build auth headers for an MCP connection.

    For auth_type='bearer': adds an Authorization: Bearer header.
    For auth_type='header': auth_secret is stored as "Header-Name:value".

    Custom headers from ``conn.headers`` are included *unless* they match
    the dangerous header blocklist (connection, transfer-encoding, host,
    x-forwarded-*, proxy-*, cookie, authorization, set-cookie,
    content-length).
    """
    headers: dict[str, str] = {}
    for raw_name, raw_value in (conn.headers or {}).items():
        if _is_dangerous_header(raw_name):
            _logger.warning("Dropping forbidden custom header %r on MCP connection %s", raw_name, conn.id)
            continue
        headers[raw_name] = raw_value
    if conn.auth_type == "bearer" and decrypted_secret:
        headers["Authorization"] = f"Bearer {decrypted_secret}"
    elif conn.auth_type == "header" and decrypted_secret:
        k, _, v = decrypted_secret.partition(":")
        headers[k.strip()] = v.strip()
    return headers


# Monotonically increasing JSON-RPC request ID
_rpc_id = count(1)


def _unwrap_mcp_result(result: dict) -> Any:
    """Normalize MCP tool result to a plain Python value."""
    content = result.get("content", [])
    if not content:
        return result
    if len(content) == 1 and content[0].get("type") == "text":
        return content[0]["text"]
    return content


async def discover_tools(
    conn: MCPConnection, *, decrypted_secret: str | None
) -> list[dict]:
    """Call tools/list on the remote MCP server. Returns raw tool manifests."""
    from noodle_nodes.http_security import assert_public_http_url

    assert_public_http_url(conn.url, context="MCP connection")
    headers = _build_auth_headers(conn, decrypted_secret)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            conn.url.rstrip("/"),
            json={"jsonrpc": "2.0", "id": next(_rpc_id), "method": "tools/list", "params": {}},
            headers={"Content-Type": "application/json", **headers},
        )
        resp.raise_for_status()
        data = resp.json()
    if "error" in data:
        raise MCPError(data["error"].get("message", "MCP tools/list error"))
    return data.get("result", {}).get("tools", [])


async def call_tool(
    conn: MCPConnection,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    decrypted_secret: str | None,
    timeout_seconds: int = 30,
) -> Any:
    """Execute a single MCP tool call and return its result."""
    from noodle_nodes.http_security import assert_public_http_url

    assert_public_http_url(conn.url, context="MCP connection")
    headers = _build_auth_headers(conn, decrypted_secret)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(
            conn.url.rstrip("/"),
            json={
                "jsonrpc": "2.0",
                "id": next(_rpc_id),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            headers={"Content-Type": "application/json", **headers},
        )
        resp.raise_for_status()
        data = resp.json()
    if "error" in data:
        raise MCPError(data["error"].get("message", "MCP tools/call error"))
    result = data.get("result", {})
    return _unwrap_mcp_result(result)


def mcp_tool_to_node_manifest(tool: dict, conn_id: str) -> dict:
    """Convert an MCP tool definition to a Noodle NodeManifest dict."""
    schema = tool.get("inputSchema", {})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    return {
        "id": f"mcp:{conn_id}:{tool['name']}",
        "name": tool.get("title") or tool["name"],
        "category": "MCP",
        "description": tool.get("description", ""),
        "input_kinds": {
            k: _JSON_TYPE_TO_PORT_KIND.get(v.get("type", ""), "any")
            for k, v in props.items()
        },
        "output_kinds": {"main": "any"},
        "params": [
            {
                "name": k,
                "label": k.replace("_", " ").title(),
                "type": v.get("type", "any"),
                "required": k in required,
                "description": v.get("description", ""),
            }
            for k, v in props.items()
        ],
        "mcp_connection_id": conn_id,
        "mcp_tool_name": tool["name"],
    }


async def _load_conn_with_secret(
    connection_id: str,
    org_id: str,
    session: AsyncSession,
) -> tuple[MCPConnection, str | None]:
    """Load an MCPConnection and decrypt its auth_secret.

    Raises ValueError if the connection is not found or the KEK is unavailable.
    auth_secret is single-field Fernet-encrypted with the org KEK.
    """
    conn = await session.scalar(
        select(MCPConnection).where(
            MCPConnection.id == connection_id,
            MCPConnection.org_id == org_id,
        )
    )
    if conn is None:
        raise ValueError(f"MCPConnection {connection_id!r} not found")
    if not conn.auth_secret:
        return conn, None
    org_kek = await get_org_kek(org_id, session)
    if org_kek is None:
        _logger.warning(
            "No KEK available for org %s — cannot decrypt auth_secret", org_id
        )
        raise ValueError(f"No KEK available for org {org_id!r}")
    from cryptography.fernet import Fernet

    secret = Fernet(org_kek).decrypt(conn.auth_secret.encode()).decode()
    return conn, secret


def encrypt_auth_secret(raw: str, org_kek: bytes) -> str:
    """Encrypt a plaintext auth_secret with the org KEK (single-field Fernet)."""
    from cryptography.fernet import Fernet

    return Fernet(org_kek).encrypt(raw.encode()).decode()
