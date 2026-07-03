"""AI Tool supplier nodes + concrete tool adapters — WP10.

These nodes output ``ToolAdapter`` instances on an ``ai_tool`` port.  The
agent engine (WP11) will dispatch ``ToolAdapter.invoke`` calls as child node
runs so every tool invocation is observable.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from nodyra.ai_runtime import ToolAdapter, ToolParameterSchema, ToolSchema
from nodyra.context import workflow_caller
from nodyra.sdk import node
from nodyra_nodes.http_security import safe_request

AI_CATEGORY = "AI"

_HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
_URL_TEMPLATE_RE = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}|\{([A-Za-z_][A-Za-z0-9_.-]*)\}"
)


def _parse_schema(raw: Any) -> ToolParameterSchema:
    """Coerce a JSON-schema-ish value into a ToolParameterSchema."""
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return ToolParameterSchema()
        try:
            raw = json.loads(raw)
        except ValueError:
            return ToolParameterSchema()
    if not isinstance(raw, dict):
        return ToolParameterSchema()
    return ToolParameterSchema(
        type=str(raw.get("type") or "object"),
        properties=raw.get("properties") if isinstance(raw.get("properties"), dict) else {},
        required=raw.get("required") if isinstance(raw.get("required"), list) else [],
    )


def collect_tool_adapters(value: Any) -> list[ToolAdapter]:
    """Collect ToolAdapter instances from a single adapter, list, or bundle."""
    if isinstance(value, ToolAdapter):
        return [value]
    if isinstance(value, (list, tuple, set, frozenset)):
        tools: list[ToolAdapter] = []
        for item in value:
            tools.extend(collect_tool_adapters(item))
        return tools
    if isinstance(value, dict):
        tools: list[ToolAdapter] = []
        for key in ("tool", "tools"):
            if key in value:
                tools.extend(collect_tool_adapters(value[key]))
        return tools
    return []


def _argument_value(arguments: dict[str, Any], key: str) -> Any:
    value: Any = arguments
    for part in key.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        raise ValueError(f"missing URL template argument: {key}")
    return value


def _render_url_template(url: str, arguments: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or ""
        value = _argument_value(arguments, key)
        return quote(str(value), safe="")

    return _URL_TEMPLATE_RE.sub(replace, url)


class HttpToolAdapter(ToolAdapter):
    """Calls an HTTP endpoint, substituting tool arguments into the request."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        parameters: ToolParameterSchema | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._name = name or "http_tool"
        self._description = description or "Call an HTTP endpoint."
        self._url = url
        self._method = (method or "GET").upper()
        self._headers = headers or {}
        self._parameters = parameters or ToolParameterSchema()
        self._timeout = int(timeout_seconds or 30)

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description=self._description,
            parameters=self._parameters,
        )

    @property
    def side_effecting(self) -> bool:
        return self._method not in {"GET", "HEAD", "OPTIONS"}

    def invoke(self, arguments: dict[str, Any]) -> str:
        if not self._url:
            raise ValueError(f"{self._name}: url is required")
        rendered_url = _render_url_template(self._url, arguments)
        kwargs: dict[str, Any] = {
            "headers": self._headers,
            "timeout": max(1, min(300, self._timeout)),
        }
        if self._method == "GET":
            kwargs["params"] = arguments
        else:
            kwargs["json"] = arguments
        # SEC-2: the URL is templated from model-supplied arguments (prompt
        # injection can steer it), so the guard re-validates every redirect hop,
        # not just the first request.
        resp = safe_request(
            self._method, rendered_url,
            context=f"{self._name} AI HTTP tool", **kwargs,
        )
        if resp.status_code >= 400:
            return f"HTTP {resp.status_code}: {resp.text[:1000]}"
        return resp.text[:8000]


class WorkflowToolAdapter(ToolAdapter):
    """Declarative reference to another workflow used as a tool.

    The adapter does not execute the workflow itself — the agent engine
    resolves ``workflow_id`` and dispatches a child run.  ``invoke`` is a
    fallback that returns a structured descriptor.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        workflow_id: str,
        parameters: ToolParameterSchema | None = None,
    ) -> None:
        self._name = name or "workflow_tool"
        self._description = description or "Run a Nodyra workflow."
        self._workflow_id = workflow_id
        self._parameters = parameters or ToolParameterSchema()

    @property
    def workflow_id(self) -> str:
        return self._workflow_id

    @property
    def side_effecting(self) -> bool:
        return True

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description=self._description,
            parameters=self._parameters,
        )

    def invoke(self, arguments: dict[str, Any]) -> str:
        return json.dumps(
            {
                "_workflow_tool": self._workflow_id,
                "arguments": arguments,
            }
        )

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        if not self._workflow_id:
            raise ValueError(f"{self._name}: workflow_id is required")
        caller = workflow_caller.get()
        if caller is None:
            raise RuntimeError(
                f"{self._name}: no host workflow caller is configured for this run"
            )
        result = await caller(self._workflow_id, arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)


@node(
    name="AI HTTP Tool",
    id="ai_http_tool",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    param_groups={"Options": ["headers", "timeout_seconds"]},
    params={
        "name": {"description": "Tool name exposed to the model (snake_case)."},
        "description": {
            "widget": "textarea",
            "description": "What the tool does — helps the model decide when to call it.",
        },
        "url": {"description": "Target URL. Supports templating of tool arguments."},
        "method": {"choices": _HTTP_METHODS, "description": "HTTP method."},
        "parameters_schema": {
            "widget": "code",
            "description": "JSON Schema for the tool's arguments (object).",
        },
        "headers": {
            "widget": "code",
            "description": "Optional JSON object of request headers.",
            "group": "Options",
        },
        "timeout_seconds": {
            "description": "HTTP timeout in seconds (1–300).",
            "group": "Options",
        },
    },
)
def ai_http_tool(
    name: str = "http_tool",
    description: str = "Call an HTTP endpoint.",
    url: str = "",
    method: str = "GET",
    parameters_schema: Any = None,
    headers: Any = None,
    timeout_seconds: int = 30,
) -> ToolAdapter:
    """Supply an HTTP-backed tool to a downstream AI Agent."""
    parsed_headers: dict[str, str] = {}
    if isinstance(headers, str) and headers.strip():
        try:
            loaded = json.loads(headers)
            if isinstance(loaded, dict):
                parsed_headers = {str(k): str(v) for k, v in loaded.items()}
        except ValueError:
            parsed_headers = {}
    elif isinstance(headers, dict):
        parsed_headers = {str(k): str(v) for k, v in headers.items()}

    return HttpToolAdapter(
        name=name,
        description=description,
        url=url,
        method=method,
        headers=parsed_headers,
        parameters=_parse_schema(parameters_schema),
        timeout_seconds=int(timeout_seconds or 30),
    )


@node(
    name="AI Workflow Tool",
    id="ai_workflow_tool",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    outputs=["tool"],
    output_kinds={"tool": "ai_tool"},
    params={
        "name": {"description": "Tool name exposed to the model (snake_case)."},
        "description": {
            "widget": "textarea",
            "description": "What the workflow does.",
        },
        "workflow_id": {
            "widget": "workflow_selector",
            "description": "The workflow to invoke when the model calls this tool.",
        },
        "parameters_schema": {
            "widget": "code",
            "description": "JSON Schema for the tool's arguments (object).",
        },
    },
)
def ai_workflow_tool(
    name: str = "workflow_tool",
    description: str = "Run a Nodyra workflow.",
    workflow_id: str = "",
    parameters_schema: Any = None,
) -> ToolAdapter:
    """Supply a workflow-backed tool to a downstream AI Agent."""
    return WorkflowToolAdapter(
        name=name,
        description=description,
        workflow_id=workflow_id,
        parameters=_parse_schema(parameters_schema),
    )


@node(
    name="AI Tool Bundle",
    id="ai_tool_bundle",
    category=AI_CATEGORY,
    role="tool",
    icon="ai",
    inputs=["tool_1", "tool_2", "tool_3", "tool_4", "tool_5"],
    input_kinds={
        "tool_1": "ai_tool",
        "tool_2": "ai_tool",
        "tool_3": "ai_tool",
        "tool_4": "ai_tool",
        "tool_5": "ai_tool",
    },
    outputs=["tools"],
    output_kinds={"tools": "ai_tool"},
    params={
        "strict": {
            "description": "Fail when no valid AI tools are connected.",
        },
    },
)
def ai_tool_bundle(
    tool_1: Any = None,
    tool_2: Any = None,
    tool_3: Any = None,
    tool_4: Any = None,
    tool_5: Any = None,
    strict: bool = False,
) -> list[ToolAdapter]:
    """Merge several tool supplier outputs into one agent tool input."""
    tools: list[ToolAdapter] = []
    seen: set[str] = set()
    for value in (tool_1, tool_2, tool_3, tool_4, tool_5):
        for adapter in collect_tool_adapters(value):
            name = str(adapter.schema.name or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            tools.append(adapter)
    if strict and not tools:
        raise ValueError("ai_tool_bundle: no valid AI tools connected")
    return tools
