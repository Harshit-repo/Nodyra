"""MCP client: connects to external MCP servers, discovers tools, executes calls."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from jsonschema import SchemaError
from jsonschema.validators import validator_for
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditEvent, MCPConnection
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
    "accept", "accept-encoding", "content-type", "mcp-session-id", "mcp-protocol-version",
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
    """Safe MCP failure with an explicit dispatch boundary."""

    execution_attempted = False


class MCPToolError(MCPError):
    """The server reported an error; any partial external effect is unknown."""


class MCPTransportError(MCPError):
    """Transport/protocol failure; do not assume a dispatched call had no effect."""


class MCPPolicyError(MCPError):
    """A changed approved contract prevented dispatch."""


def ensure_tool_allowed(conn: MCPConnection, tool_name: str) -> None:
    """Raise ValueError when this connection's call policy forbids the tool."""
    if getattr(conn, "enabled", True) is False:
        raise ValueError(f"MCP connection {conn.name!r} is disabled")
    allowed = getattr(conn, "allowed_tools", None)
    if allowed is None:
        return
    # Fail closed on a malformed policy: `in` on a stored string would be a
    # substring match, silently allowing tools the user never listed.
    if not isinstance(allowed, (list, tuple)):
        raise ValueError(
            f"MCP connection {conn.name!r} has a malformed allowed_tools policy"
        )
    if tool_name not in allowed:
        raise ValueError(
            f"MCP tool {tool_name!r} is not in this connection's allowed tools"
        )


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
        if not k.strip() or (_is_dangerous_header(k.strip()) and k.strip().lower() != "authorization"):
            raise MCPPolicyError("Invalid MCP credential header")
        headers[k.strip()] = v.strip()
    return headers


# Each operation owns its HTTP client and request IDs. No response may be
# accepted for a different request, even when the remote server returns HTTP 200.
_MAX_DISCOVERY_PAGES = 32
_MAX_DISCOVERY_TOOLS = 2048
_MAX_CURSOR_LENGTH = 4096
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
_SSE_BOUNDARY = re.compile(br"\r\n\r\n|\n\n|\r\r")


def _decode_json(body: bytes | bytearray) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError("Non-finite JSON value")

    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=object_pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise MCPTransportError("Invalid MCP JSON response") from exc


def _rpc_result(data: Any, request_id: int) -> dict[str, Any]:
    if (
        not isinstance(data, dict)
        or data.get("jsonrpc") != "2.0"
        or type(data.get("id")) is not int
        or data["id"] != request_id
        or ("result" in data) == ("error" in data)
    ):
        raise MCPTransportError("Invalid MCP JSON-RPC response envelope")
    if "error" in data:
        error = data["error"]
        if (
            not isinstance(error, dict)
            or type(error.get("code")) is not int
            or not isinstance(error.get("message"), str)
        ):
            raise MCPTransportError("Invalid MCP JSON-RPC error response")
        # Remote messages/data may contain credentials or personal data. They
        # must not flow into run errors or the centralized audit log.
        raise MCPToolError("Tool execution failed (MCP JSON-RPC error)")
    if not isinstance(data["result"], dict):
        raise MCPTransportError("Invalid MCP JSON-RPC result")
    return data["result"]


async def _post_message(
    client: httpx.AsyncClient,
    url: str,
    *,
    message: dict[str, Any],
    headers: dict[str, str],
    extensions: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Receive JSON or SSE without unbounded buffering or server-initiated work."""
    expects_result = "method" in message and "id" in message
    async with client.stream(
        "POST", url,
        json=message,
        headers=headers,
        extensions=extensions,
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise MCPTransportError("Compressed MCP responses are not supported")
        session_id = response.headers.get("mcp-session-id")
        if session_id is not None and (
            not session_id or len(session_id) > 256
            or any(ord(char) < 33 or ord(char) > 126 for char in session_id)
        ):
            raise MCPTransportError("Invalid MCP session ID")
        if not expects_result and response.status_code != 202:
            raise MCPTransportError("MCP notification was not accepted")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        is_sse = media_type == "text/event-stream"
        if expects_result and media_type not in {"application/json", "text/event-stream"}:
            raise MCPTransportError("Unsupported MCP response content type")
        # Cap all received bytes, including SSE notifications. Compression is
        # disabled above so decompression cannot allocate an unbounded chunk.
        body = bytearray()
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > settings.mcp_max_response_bytes:
                raise MCPTransportError("MCP response too large")
            body.extend(chunk)
            if not expects_result and body:
                raise MCPTransportError("MCP notification response must be empty")
            while is_sse and (boundary := _SSE_BOUNDARY.search(body)) is not None:
                event = bytes(body[:boundary.start()])
                del body[:boundary.end()]
                data_lines = [line[5:].removeprefix(b" ") for line in event.splitlines() if line.startswith(b"data:")]
                if not data_lines:
                    continue
                data = _decode_json(b"\n".join(data_lines))
                if isinstance(data, dict) and "method" in data:
                    if data.get("jsonrpc") != "2.0" or not isinstance(data["method"], str):
                        raise MCPTransportError("Invalid MCP server notification")
                    if "id" in data:
                        # No sampling/elicitation capabilities are advertised.
                        # Ping is the only server request supported by this client.
                        if data["method"] != "ping" or type(data["id"]) not in (int, str):
                            raise MCPTransportError("Unsupported MCP server request")
                        await _post_message(
                            client, url,
                            message={"jsonrpc": "2.0", "id": data["id"], "result": {}},
                            headers=headers, extensions=extensions,
                        )
                    continue
                return _rpc_result(data, message["id"]), session_id
    if not expects_result:
        return None, session_id
    if is_sse:
        raise MCPTransportError("MCP event stream ended without a response")
    return _rpc_result(_decode_json(body), message["id"]), session_id


class _MCPSession:
    def __init__(self, client: httpx.AsyncClient, conn: MCPConnection, secret: str | None):
        from nodyra_nodes.httpx_security import pinned_request_kwargs, resolve_pinned

        pinned = resolve_pinned(conn.url, context="MCP connection")
        extra = pinned_request_kwargs(pinned)
        self.client = client
        self.url = pinned.url
        self.headers = {
            **_build_auth_headers(conn, secret), **extra["headers"],
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
        }
        self.extensions = extra["extensions"]
        self.next_id = 1
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self.protocol_version = ""

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        result, session_id = await _post_message(
            self.client, self.url,
            message={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            headers=self.headers, extensions=self.extensions,
        )
        if method == "initialize" and session_id is not None:
            self.headers["Mcp-Session-Id"] = session_id
        assert result is not None
        return result

    async def initialize(self) -> None:
        result = await self.request("initialize", {
            "protocolVersion": _PROTOCOL_VERSIONS[0], "capabilities": {},
            "clientInfo": {"name": "nodyra", "version": "1.0"},
        })
        protocol = result.get("protocolVersion")
        capabilities = result.get("capabilities")
        server_info = result.get("serverInfo")
        if protocol not in _PROTOCOL_VERSIONS:
            raise MCPTransportError("Unsupported MCP protocol version")
        if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
            raise MCPTransportError("MCP server does not advertise tool capability")
        if any(not isinstance(value, dict) for value in capabilities.values()):
            raise MCPTransportError("Invalid MCP server capability")
        if "listChanged" in capabilities["tools"] and not isinstance(capabilities["tools"]["listChanged"], bool):
            raise MCPTransportError("Invalid MCP tools capability")
        if not isinstance(server_info, dict) or not all(isinstance(server_info.get(field), str) and server_info[field] for field in ("name", "version")):
            raise MCPTransportError("Invalid MCP server information")
        self.protocol_version = protocol
        self.capabilities = capabilities
        self.server_info = server_info
        self.headers["MCP-Protocol-Version"] = protocol
        await _post_message(
            self.client, self.url,
            message={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self.headers, extensions=self.extensions,
        )

    async def close(self) -> None:
        if "Mcp-Session-Id" not in self.headers:
            return
        try:
            async with asyncio.timeout(2.0):
                async with self.client.stream(
                    "DELETE", self.url, headers=self.headers, extensions=self.extensions,
                ):
                    pass  # 405 is allowed when servers do not support session termination.
        except (httpx.HTTPError, TimeoutError):
            _logger.debug("MCP session cleanup failed")


def _validate_tool_manifest(tool: Any) -> None:
    """Reject malformed manifests before storing or exposing them as nodes."""
    if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
        raise MCPTransportError("Invalid MCP tool manifest")
    if _TOOL_NAME.fullmatch(tool["name"]) is None:
        raise MCPTransportError("Invalid MCP tool name")
    for field in ("title", "description"):
        if field in tool and not isinstance(tool[field], str):
            raise MCPTransportError("Invalid MCP tool metadata")
    for field in ("_meta", "execution"):
        if field in tool and not isinstance(tool[field], dict):
            raise MCPTransportError("Invalid MCP tool metadata")
    for field in ("inputSchema", "outputSchema"):
        if field == "outputSchema" and field not in tool:
            continue
        schema = tool.get(field)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise MCPTransportError("MCP tool schema must describe an object")
        try:
            validator_for(schema).check_schema(schema)
        except (SchemaError, ValueError, TypeError, RecursionError) as exc:
            raise MCPTransportError("Invalid MCP tool JSON schema") from exc
    annotations = tool.get("annotations")
    if annotations is not None:
        if not isinstance(annotations, dict):
            raise MCPTransportError("Invalid MCP tool annotations")
        for field in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            if field in annotations and not isinstance(annotations[field], bool):
                raise MCPTransportError("Invalid MCP tool annotation hint")


def _validate_call_result(result: dict[str, Any]) -> None:
    if "_meta" in result and not isinstance(result["_meta"], dict):
        raise MCPTransportError("Invalid MCP tool result metadata")
    if "isError" in result and not isinstance(result["isError"], bool):
        raise MCPTransportError("Invalid MCP tool error flag")
    if result.get("isError") is True:
        raise MCPToolError("Tool execution failed (MCP isError)")
    if "structuredContent" in result and not isinstance(result["structuredContent"], dict):
        raise MCPTransportError("Invalid MCP structured tool result")
    content = result.get("content")
    if not isinstance(content, list):
        raise MCPTransportError("Invalid MCP tool content")
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise MCPTransportError("Invalid MCP tool content block")
        if block["type"] == "text" and not isinstance(block.get("text"), str):
            raise MCPTransportError("Invalid MCP text content")


def _audit_error(exc: Exception) -> str:
    """Do not retain remote bodies, URL query credentials, or exception details."""
    if isinstance(exc, MCPError):
        return str(exc)
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "MCP request timed out; external outcome is unknown"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"MCP HTTP request failed (status {exc.response.status_code})"
    return "MCP request failed"


def _unwrap_mcp_result(result: dict) -> Any:
    """Normalize MCP tool result to a plain Python value."""
    if "structuredContent" in result:
        return result["structuredContent"]
    content = result.get("content", [])
    if not content:
        return result
    if len(content) == 1 and content[0].get("type") == "text":
        return content[0]["text"]
    return content


def record_mcp_tool_call(
    session: AsyncSession,
    conn: MCPConnection,
    tool_name: str,
    *,
    ok: bool,
    run_id: str | None = None,
    actor_id: str | None = None,
    actor_email: str | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Append a per-connection MCP tool-call audit record."""
    detail = {
        "connection_id": conn.id,
        "connection_name": conn.name,
        "tool": tool_name,
        "ok": ok,
        "run_id": run_id,
        "duration_ms": duration_ms,
        "error": error,
    }
    session.add(
        AuditEvent(
            org_id=conn.org_id,
            action="mcp_tool_call",
            target_type="mcp_connection",
            target_id=conn.id,
            detail=json.dumps(detail, ensure_ascii=False, sort_keys=True),
            actor_id=actor_id,
            actor_email=actor_email,
        )
    )


async def _commit_mcp_tool_audit(
    session: AsyncSession | None,
    conn: MCPConnection,
    tool_name: str,
    *,
    ok: bool,
    run_id: str | None,
    actor_id: str | None,
    actor_email: str | None,
    duration_ms: int | None,
    error: str | None = None,
) -> None:
    if session is None:
        return
    try:
        record_mcp_tool_call(
            session,
            conn,
            tool_name,
            ok=ok,
            run_id=run_id,
            actor_id=actor_id,
            actor_email=actor_email,
            duration_ms=duration_ms,
            error=error,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        _logger.warning("Failed to write MCP tool-call audit", exc_info=True)


async def _discover_session_tools(remote: _MCPSession) -> list[dict]:
    tools: list[dict] = []
    names: set[str] = set()
    cursors: set[str] = set()
    params: dict[str, Any] = {}
    for _ in range(_MAX_DISCOVERY_PAGES):
        result = await remote.request("tools/list", params)
        page = result.get("tools")
        if not isinstance(page, list):
            raise MCPTransportError("Invalid MCP tools/list result")
        if len(tools) + len(page) > _MAX_DISCOVERY_TOOLS:
            raise MCPTransportError("MCP tool discovery exceeded its tool limit")
        for tool in page:
            _validate_tool_manifest(tool)
            if tool["name"] in names:
                raise MCPTransportError("Duplicate MCP tool name")
            names.add(tool["name"])
            tools.append(tool)
        cursor = result.get("nextCursor")
        if cursor is None:
            return tools
        if not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
            raise MCPTransportError("Invalid MCP tools/list cursor")
        if cursor in cursors:
            raise MCPTransportError("MCP tool discovery cursor cycle")
        cursors.add(cursor)
        params = {"cursor": cursor}
    raise MCPTransportError("MCP tool discovery exceeded its page limit")


async def discover_catalog(
    conn: MCPConnection, *, decrypted_secret: str | None,
) -> dict[str, Any]:
    """Negotiate a real MCP session and return a bounded, validated catalog."""
    try:
        async with asyncio.timeout(30.0), httpx.AsyncClient(timeout=10.0) as client:
            remote = _MCPSession(client, conn, decrypted_secret)
            try:
                await remote.initialize()
                tools = await _discover_session_tools(remote)
                return {
                    "tools": tools, "capabilities": remote.capabilities,
                    "server_info": remote.server_info, "protocol_version": remote.protocol_version,
                }
            finally:
                await remote.close()
    except (httpx.HTTPError, TimeoutError) as exc:
        raise MCPTransportError(_audit_error(exc)) from exc


async def discover_tools(
    conn: MCPConnection, *, decrypted_secret: str | None,
) -> list[dict]:
    return (await discover_catalog(conn, decrypted_secret=decrypted_secret))["tools"]


def tool_contract(tool: dict[str, Any]) -> dict[str, Any]:
    """Canonical security contract; exclude presentation-only schema annotations.

    A property *named* ``description`` or a value within ``const`` is data and
    must not disappear when stripping JSON Schema's descriptive keywords.
    """
    schema_maps = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies"}
    schema_values = {"items", "additionalItems", "contains", "additionalProperties", "unevaluatedProperties", "unevaluatedItems", "propertyNames", "not", "if", "then", "else"}
    schema_lists = {"allOf", "anyOf", "oneOf", "prefixItems"}

    def clean_schema(schema: Any) -> Any:
        if not isinstance(schema, dict):
            return schema
        result: dict[str, Any] = {}
        for key, value in schema.items():
            if key in {"description", "title", "$comment", "examples"}:
                continue
            if key in schema_maps and isinstance(value, dict):
                result[key] = {name: clean_schema(child) for name, child in value.items()}
            elif key in schema_values:
                result[key] = [clean_schema(child) for child in value] if isinstance(value, list) else clean_schema(value)
            elif key in schema_lists and isinstance(value, list):
                result[key] = [clean_schema(child) for child in value]
            else:
                result[key] = value
        return result

    result = {key: tool[key] for key in ("name", "inputSchema", "outputSchema", "annotations", "execution", "_meta") if key in tool}
    for field in ("inputSchema", "outputSchema"):
        if field in result:
            result[field] = clean_schema(result[field])
    if isinstance(result.get("annotations"), dict):
        result["annotations"] = {key: value for key, value in result["annotations"].items() if key != "title"}
    return result


async def call_tool(
    conn: MCPConnection,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    decrypted_secret: str | None,
    timeout_seconds: float | None = None,
    audit_session: AsyncSession | None = None,
    run_id: str | None = None,
    actor_id: str | None = None,
    actor_email: str | None = None,
    return_envelope: bool = False,
    expected_tool: dict[str, Any] | None = None,
    expected_capabilities: dict[str, Any] | None = None,
    before_dispatch: Callable[[], Awaitable[None]] | None = None,
) -> Any:
    """Execute once; failures never retry a potentially effectful tool call."""
    started = time.monotonic()
    timeout_seconds = timeout_seconds or settings.mcp_tool_timeout_seconds
    dispatched = False
    try:
        async with asyncio.timeout(timeout_seconds), httpx.AsyncClient(timeout=timeout_seconds) as client:
            remote = _MCPSession(client, conn, decrypted_secret)
            try:
                await remote.initialize()
                if expected_capabilities is not None and remote.capabilities != expected_capabilities:
                    raise MCPPolicyError("MCP server capabilities changed; review and synchronize the connection")
                if expected_tool is not None:
                    tools = await _discover_session_tools(remote)
                    current_tool = next((tool for tool in tools if tool["name"] == tool_name), None)
                    if current_tool is None or tool_contract(current_tool) != tool_contract(expected_tool):
                        raise MCPPolicyError("MCP tool contract changed; review and synchronize the connection")
                if before_dispatch is not None:
                    await before_dispatch()
                dispatched = True
                result = await remote.request("tools/call", {"name": tool_name, "arguments": arguments})
            finally:
                await remote.close()
        _validate_call_result(result)
    except Exception as exc:
        failure = MCPTransportError(_audit_error(exc)) if isinstance(exc, (httpx.HTTPError, TimeoutError)) else exc
        if isinstance(failure, MCPError):
            failure.execution_attempted = dispatched
        await _commit_mcp_tool_audit(
            audit_session,
            conn,
            tool_name,
            ok=False,
            run_id=run_id,
            actor_id=actor_id,
            actor_email=actor_email,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=_audit_error(failure),
        )
        if failure is not exc:
            raise failure from exc
        raise
    await _commit_mcp_tool_audit(
        audit_session,
        conn,
        tool_name,
        ok=True,
        run_id=run_id,
        actor_id=actor_id,
        actor_email=actor_email,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result if return_envelope else _unwrap_mcp_result(result)


def mcp_tool_to_node_manifest(tool: dict, conn_id: str) -> dict:
    """Convert an MCP tool definition to a Nodyra NodeManifest dict."""
    schema = tool.get("inputSchema", {})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    props = {key: value if isinstance(value, dict) else {} for key, value in props.items()}

    def property_type(value: dict) -> str:
        kind = value.get("type")
        return kind if isinstance(kind, str) else "any"
    return {
        "id": f"mcp:{conn_id}:{tool['name']}",
        "name": tool.get("title") or tool["name"],
        "category": "MCP",
        "description": tool.get("description", ""),
        "input_kinds": {
            k: _JSON_TYPE_TO_PORT_KIND.get(property_type(v), "any")
            for k, v in props.items()
        },
        "output_kinds": {"main": "any"},
        "params": [
            {
                "name": k,
                "label": k.replace("_", " ").title(),
                "type": property_type(v),
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
