"""MCP client nodes: consume external MCP servers from workflows.

``mcp_tools`` supplies a remote server's tools to a downstream AI Agent
(every tool becomes a :class:`ToolAdapter`); ``mcp_call_tool`` invokes one
named tool in the data flow; ``mcp_list_tools`` returns the server's tool
descriptors. Transport is streamable HTTP only — stdio servers would mean
spawning arbitrary processes on the worker host. URLs pass the same SSRF
guard as the AI HTTP tool.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from noodle.ai_runtime import ToolAdapter, ToolParameterSchema, ToolSchema
from noodle.sdk import node
from noodle_nodes.http_security import assert_public_http_url

AI_CATEGORY = "AI"

_CREDENTIAL_META = {
    "credential": {
        "type": "mcp_server",
        "label": "MCP Server",
        "multi": True,
        "fields": ["url", "auth_token", "headers_json"],
    },
    "description": "Stored MCP server connection (URL + optional bearer token).",
}


@dataclass(frozen=True)
class McpServerConfig:
    url: str
    headers: dict[str, str] = field(default_factory=dict)


def _config_from_credentials(credentials: Any) -> McpServerConfig:
    if not isinstance(credentials, dict):
        raise ValueError("mcp: connect an MCP Server credential (url required)")
    url = str(credentials.get("url") or "").strip()
    if not url:
        raise ValueError("mcp: credential is missing the server url")
    headers: dict[str, str] = {}
    raw_headers = credentials.get("headers_json")
    if isinstance(raw_headers, str) and raw_headers.strip():
        try:
            loaded = json.loads(raw_headers)
            if isinstance(loaded, dict):
                headers.update({str(k): str(v) for k, v in loaded.items()})
        except ValueError:
            pass
    elif isinstance(raw_headers, dict):
        headers.update({str(k): str(v) for k, v in raw_headers.items()})
    token = str(credentials.get("auth_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return McpServerConfig(url=url, headers=headers)


@asynccontextmanager
async def _mcp_session(config: McpServerConfig):
    """Open an initialized MCP client session against ``config``."""
    assert_public_http_url(config.url, context="MCP server")
    async with streamablehttp_client(config.url, headers=config.headers or None) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


def _to_param_schema(input_schema: Any) -> ToolParameterSchema:
    if not isinstance(input_schema, dict):
        return ToolParameterSchema()
    return ToolParameterSchema(
        type=str(input_schema.get("type") or "object"),
        properties=(
            input_schema.get("properties")
            if isinstance(input_schema.get("properties"), dict)
            else {}
        ),
        required=(
            input_schema.get("required")
            if isinstance(input_schema.get("required"), list)
            else []
        ),
    )


def _result_to_value(result: Any) -> Any:
    """Convert an MCP tool result to a Python value.

    Handles text, image content items, and structuredContent.
    Returns image dicts for image content so callers don't silently lose data.
    """
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    items: list[Any] = []
    for item in getattr(result, "content", None) or []:
        if getattr(item, "type", None) == "image":
            items.append(
                {
                    "_image": True,
                    "data": getattr(item, "data", "") or "",
                    "mime_type": getattr(item, "mimeType", "") or "",
                }
            )
        else:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                items.append(text)
    if len(items) == 1:
        return items[0]
    return items if items else ""


class McpToolAdapter(ToolAdapter):
    """Calls one remote MCP tool; session opened once and reused across calls."""

    def __init__(
        self,
        *,
        config: McpServerConfig,
        schema: ToolSchema,
        side_effecting: bool = True,
    ) -> None:
        self._config = config
        self._schema = schema
        self._side_effecting = bool(side_effecting)
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    @property
    def side_effecting(self) -> bool:
        return self._side_effecting

    def invoke(self, arguments: dict[str, Any]) -> str:
        raise RuntimeError(
            f"{self._schema.name}: MCP tools are async-only (invoke_async)"
        )

    async def _ensure_session(self) -> ClientSession:
        if self._session is not None:
            return self._session
        stack = AsyncExitStack()
        try:
            session = await stack.enter_async_context(_mcp_session(self._config))
        except BaseException:
            await stack.aclose()
            raise
        self._exit_stack = stack
        self._session = session
        return session

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        try:
            session = await self._ensure_session()
            result = await session.call_tool(self._schema.name, dict(arguments or {}))
        except Exception:
            # Session-level failure — reset so the next call gets a fresh session.
            if self._exit_stack is not None:
                try:
                    await self._exit_stack.aclose()
                except Exception:
                    pass
            self._session = None
            self._exit_stack = None
            raise
        if getattr(result, "isError", False):
            value = _result_to_value(result)
            text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
            raise RuntimeError(text or f"{self._schema.name}: tool returned an error")
        value = _result_to_value(result)
        return json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value


def _parse_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, str):
        try:
            loaded = json.loads(arguments)
        except ValueError as exc:
            raise ValueError(f"mcp: arguments is not valid JSON: {exc}") from exc
        arguments = loaded
    if not isinstance(arguments, dict):
        raise ValueError("mcp: arguments must be a JSON object")
    return arguments


@node(
    name="MCP Tools",
    id="mcp_tools",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tools"],
    output_kinds={"tools": "ai_tool"},
    params={
        "credentials": _CREDENTIAL_META,
        "tool_filter": {
            "description": "Optional comma-separated tool names to expose (default: all).",
        },
        "side_effecting": {
            "description": (
                "Treat the server's tools as side-effecting so agent approval "
                "gating applies (recommended for write-capable servers)."
            ),
        },
    },
)
async def mcp_tools(
    credentials: Any = None,
    tool_filter: str = "",
    side_effecting: bool = True,
) -> list[ToolAdapter]:
    """Supply an external MCP server's tools to a downstream AI Agent."""
    config = _config_from_credentials(credentials)
    async with _mcp_session(config) as session:
        listing = await session.list_tools()
    allowed = {t.strip() for t in str(tool_filter or "").split(",") if t.strip()}
    adapters: list[ToolAdapter] = []
    for tool in listing.tools:
        if allowed and tool.name not in allowed:
            continue
        schema = ToolSchema(
            name=tool.name,
            description=tool.description or tool.name,
            parameters=_to_param_schema(getattr(tool, "inputSchema", None)),
        )
        adapters.append(
            McpToolAdapter(
                config=config, schema=schema, side_effecting=side_effecting
            )
        )
    return adapters


@node(
    name="MCP Call Tool",
    id="mcp_call_tool",
    category=AI_CATEGORY,
    icon="ai",
    params={
        "credentials": _CREDENTIAL_META,
        "tool_name": {"description": "Name of the remote tool to call."},
        "arguments": {
            "widget": "code",
            "description": "Tool arguments as a JSON object (or expression).",
        },
    },
)
async def mcp_call_tool(
    input: Any = None,
    credentials: Any = None,
    tool_name: str = "",
    arguments: Any = None,
) -> Any:
    """Call one tool on an external MCP server and return its result."""
    name = str(tool_name or "").strip()
    if not name:
        raise ValueError("mcp_call_tool: tool_name is required")
    config = _config_from_credentials(credentials)
    args = _parse_arguments(arguments)
    if not args and isinstance(input, dict):
        args = input
    async with _mcp_session(config) as session:
        result = await session.call_tool(name, dict(args or {}))
    value = _result_to_value(result)
    if getattr(result, "isError", False):
        text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
        raise RuntimeError(text or f"{name}: tool returned an error")
    return value


@node(
    name="MCP List Tools",
    id="mcp_list_tools",
    category=AI_CATEGORY,
    icon="ai",
    tool_side_effecting=False,
    params={"credentials": _CREDENTIAL_META},
)
async def mcp_list_tools(input: Any = None, credentials: Any = None) -> list[dict]:
    """List the tools an external MCP server exposes."""
    config = _config_from_credentials(credentials)
    async with _mcp_session(config) as session:
        listing = await session.list_tools()
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": getattr(tool, "inputSchema", None) or {},
        }
        for tool in listing.tools
    ]


@node(
    name="MCP List Resources",
    id="mcp_list_resources",
    category=AI_CATEGORY,
    icon="ai",
    tool_side_effecting=False,
    params={"credentials": _CREDENTIAL_META},
)
async def mcp_list_resources(input: Any = None, credentials: Any = None) -> list[dict]:
    """List the resources an external MCP server exposes."""
    config = _config_from_credentials(credentials)
    async with _mcp_session(config) as session:
        listing = await session.list_resources()
    return [
        {
            "uri": str(r.uri),
            "name": getattr(r, "name", "") or "",
            "description": getattr(r, "description", "") or "",
            "mime_type": getattr(r, "mimeType", "") or "",
        }
        for r in listing.resources
    ]


@node(
    name="MCP Read Resource",
    id="mcp_read_resource",
    category=AI_CATEGORY,
    icon="ai",
    tool_side_effecting=False,
    params={
        "credentials": _CREDENTIAL_META,
        "resource_uri": {
            "description": "URI of the resource to read (e.g. noodle://workflow/abc123).",
        },
    },
)
async def mcp_read_resource(
    input: Any = None,
    credentials: Any = None,
    resource_uri: str = "",
) -> Any:
    """Read a resource from an external MCP server by URI."""
    uri = str(resource_uri or "").strip()
    if not uri and isinstance(input, str):
        uri = input.strip()
    if not uri:
        raise ValueError("mcp_read_resource: resource_uri is required")
    config = _config_from_credentials(credentials)
    async with _mcp_session(config) as session:
        result = await session.read_resource(uri)
    contents = getattr(result, "contents", None) or []
    if not contents:
        return None
    item = contents[0]
    text = getattr(item, "text", None)
    if text is not None:
        try:
            return json.loads(text)
        except ValueError:
            return text
    blob = getattr(item, "blob", None)
    if blob is not None:
        return {"_blob": True, "data": blob, "mime_type": getattr(item, "mimeType", "") or ""}
    return None


@node(
    name="MCP Get Prompt",
    id="mcp_get_prompt",
    category=AI_CATEGORY,
    icon="ai",
    tool_side_effecting=False,
    params={
        "credentials": _CREDENTIAL_META,
        "prompt_name": {"description": "Name of the prompt to retrieve."},
        "prompt_arguments": {
            "widget": "code",
            "description": "Prompt arguments as a JSON object.",
        },
    },
)
async def mcp_get_prompt(
    input: Any = None,
    credentials: Any = None,
    prompt_name: str = "",
    prompt_arguments: Any = None,
) -> Any:
    """Retrieve a named prompt template from an external MCP server."""
    name = str(prompt_name or "").strip()
    if not name:
        raise ValueError("mcp_get_prompt: prompt_name is required")
    config = _config_from_credentials(credentials)
    args = _parse_arguments(prompt_arguments) if prompt_arguments else {}
    async with _mcp_session(config) as session:
        result = await session.get_prompt(name, args or None)
    messages = getattr(result, "messages", None) or []
    return {
        "description": getattr(result, "description", "") or "",
        "messages": [
            {
                "role": getattr(m, "role", "") or "",
                "content": (
                    getattr(getattr(m, "content", None), "text", None)
                    or str(getattr(m, "content", "") or "")
                ),
            }
            for m in messages
        ],
    }
