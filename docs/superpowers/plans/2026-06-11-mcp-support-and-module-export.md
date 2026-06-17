# MCP Support (Server + Client) and Code-First Module Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (A) Noodle exposes an MCP server so external AI agents can run workflows and build/edit workflows over the Model Context Protocol; (B) Noodle workflows can consume external MCP servers (tools for the AI Agent node + direct call nodes); (C) a new "code-first" export mode that emits each workflow node as an `@node`-decorated Python function plus a `main()` that runs the workflow through the engine with identical semantics.

**Architecture:**
- **MCP server** is a hand-rolled JSON-RPC 2.0 endpoint at `POST /mcp` inside the existing FastAPI app (streamable-HTTP transport, *stateless JSON mode* — the MCP spec allows a server to answer each POST with a single `application/json` body and operate without sessions). No new server-side dependency; it reuses existing auth (`Bearer` session tokens), RBAC (`_PERMISSION_MIN_ROLE`), org resolution (global dependency), `start_run`, and the run-wait helpers already used by synchronous webhooks. Tools come in two groups: a static set of builder/runner tools, and one dynamic tool per workflow that has opted in via new `mcp_*` columns.
- **MCP client** lives in `packages/nodes` using the official `mcp` Python SDK (streamable-HTTP client transport). A new `mcp_server` credential type stores URL + auth. Three nodes: `mcp_tools` (role=tool supplier → feeds the AI Agent's tools port via `ToolAdapter`s), `mcp_call_tool` (executable node), `mcp_list_tools` (executable discovery node).
- **Module export** adds `noodle_exporter/module_codegen.py`. Each graph node becomes an `@node`-decorated wrapper function whose keyword defaults are the node's configured params and whose `wires={...}` declare its incoming edges; the body delegates to the built-in implementation so every node is directly callable. `main()` reassembles a `WorkflowGraph` from the decorated functions (params come from signature defaults via `inspect`) and executes through `noodle.engine.run` — because engine semantics key off node *type* strings (trigger detection, loop regions, dataset promotion), the rebuilt graph keeps original types via a generated `_DELEGATES` map. Hand-added `@node` functions (ids not in `_DELEGATES`) register as custom nodes and execute directly.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Pydantic v2, `mcp>=1.9` (client only), pytest (+ `httpx.AsyncClient` ASGI fixture `client` from `apps/api/tests/conftest.py`), React/TypeScript (small UI touches).

**Phases are independently shippable, in order A → B → C.** Run API tests with `python -m pytest apps/api/tests/<file> -v` and package tests with `python -m pytest packages/<pkg>/tests/<file> -v` from the repo root (activate the repo venv first; on Windows: `.venv\Scripts\Activate.ps1` or use the venv's python directly).

**Branch:** create `feat/mcp-support` off `main` before Task A1.

---

## Design contract (read first, applies to all tasks)

1. **MCP protocol surface (server):** methods `initialize`, `ping`, `tools/list`, `tools/call`; notifications (no `id` field) are acknowledged with HTTP 202 and an empty body; JSON-RPC batch arrays are rejected; `GET /mcp` and `DELETE /mcp` return 405 (no server-initiated SSE streams). Protocol versions accepted: `2025-06-18`, `2025-03-26`, `2024-11-05` (echo the client's if recognized, else ours).
2. **Auth (server):** identical posture to the rest of the API. `Authorization: Bearer <session token>` resolved with the existing `current_user`; missing token is allowed only when `settings.auth_required` is false (local dev). Per-tool permission uses the existing `_PERMISSION_MIN_ROLE` map: run tools → `workflow:run`, build tools → `workflow:write`, read-only tools → no permission. Tool-level failures (including permission denials) are returned as MCP tool results with `isError: true` so the calling model can read and recover; transport-level auth failures return HTTP 401.
3. **Tool errors:** handlers raise `McpToolError("human readable message")`; the router converts it (and any unexpected exception) into `{"content":[{"type":"text","text": msg}], "isError": true}`. Never let an exception escape as a 500.
4. **Org scoping:** `/mcp` is part of the FastAPI app, so the existing global `resolve_org` dependency already sets the org ContextVar from `X-Org-Id`. Do not add any org logic in MCP code.
5. **MCP client transport:** streamable HTTP only (no stdio in v1 — spawning local processes from workers is a security non-starter). Every server URL passes `assert_public_http_url` (SSRF guard, same as the AI HTTP tool).
6. **Module export contract:** editing a generated function's *param defaults* or *wires* changes the run; editing a function *body* does NOT change the engine-driven run for delegate wrappers (the engine executes the original built-in; the body exists so the node is callable standalone). This is stated in the generated file's docstring. Adding a *new* `@node` function with a fresh id (not in `_DELEGATES`) adds a real custom node that the engine executes directly.

---

# Phase A — MCP Server

### Task A1: MCP protocol module

**Files:**
- Create: `apps/api/app/mcp/__init__.py`
- Create: `apps/api/app/mcp/protocol.py`
- Test: `apps/api/tests/test_mcp_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_mcp_protocol.py
from app.mcp.protocol import (
    initialize_result,
    jsonrpc_error,
    jsonrpc_result,
    tool_result,
)


def test_jsonrpc_result_envelope() -> None:
    out = jsonrpc_result(7, {"ok": True})
    assert out == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


def test_jsonrpc_error_envelope() -> None:
    out = jsonrpc_error(7, -32601, "Method not found", data={"method": "x"})
    assert out["error"]["code"] == -32601
    assert out["error"]["data"] == {"method": "x"}


def test_initialize_echoes_known_version() -> None:
    assert initialize_result("2025-03-26")["protocolVersion"] == "2025-03-26"


def test_initialize_falls_back_for_unknown_version() -> None:
    assert initialize_result("1999-01-01")["protocolVersion"] == "2025-06-18"
    assert initialize_result(None)["serverInfo"]["name"] == "noodle"


def test_tool_result_wraps_dict_with_structured_content() -> None:
    out = tool_result({"a": 1})
    assert out["isError"] is False
    assert out["structuredContent"] == {"a": 1}
    assert out["content"][0]["type"] == "text"
    assert '"a": 1' in out["content"][0]["text"]


def test_tool_result_error_is_plain_text() -> None:
    out = tool_result("boom", is_error=True)
    assert out["isError"] is True
    assert "structuredContent" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/api/tests/test_mcp_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.mcp'`

- [ ] **Step 3: Write the implementation**

`apps/api/app/mcp/__init__.py` — empty file.

```python
# apps/api/app/mcp/protocol.py
"""MCP server protocol layer: JSON-RPC 2.0 + MCP envelope helpers.

Implements the MCP streamable-HTTP transport in *stateless JSON mode*: each
request is a single JSON-RPC message POSTed to ``/mcp`` and answered with a
single ``application/json`` response. The MCP spec permits this — a server
MAY return JSON instead of an SSE stream and MAY operate without sessions.
No server-initiated streams, no resumability, no ``Mcp-Session-Id``.
"""

import json
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2025-06-18", "2025-03-26", "2024-11-05"}
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SERVER_INFO = {"name": "noodle", "version": "0.0.1"}
SERVER_CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/api/tests/test_mcp_protocol.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp apps/api/tests/test_mcp_protocol.py
git commit -m "feat(api): MCP protocol layer - JSON-RPC envelopes, stateless streamable-HTTP mode"
```

---

### Task A2: Workflow `mcp_*` columns (model + migration + schemas + PUT support)

**Files:**
- Modify: `apps/api/app/models.py` (class `Workflow`, around line 290–334)
- Create: `apps/api/alembic/versions/0050_workflow_mcp.py`
- Modify: `apps/api/app/schemas.py` (`WorkflowUpdate`, `WorkflowDetail`)
- Modify: `apps/api/app/routers/workflows.py` (`update_workflow`, `_detail`)
- Test: `apps/api/tests/test_mcp_server.py` (first test in the file)

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_mcp_server.py` with:

```python
import re

from httpx import AsyncClient

MANUAL_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {},
            "position": {"x": 0, "y": 0},
        }
    ],
    "edges": [],
}


def rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


async def make_workflow(client: AsyncClient, name: str = "Mcp WF") -> str:
    workflow_id = (await client.post("/workflows", json={"name": name})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": MANUAL_GRAPH})
    return workflow_id


async def test_workflow_mcp_settings_roundtrip(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client)
    resp = await client.put(
        f"/workflows/{workflow_id}",
        json={
            "mcp_enabled": True,
            "mcp_tool_name": "my_tool",
            "mcp_description": "Does a thing",
            "mcp_parameters_schema": {"type": "object", "properties": {}},
        },
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["mcp_enabled"] is True
    assert detail["mcp_tool_name"] == "my_tool"
    assert detail["mcp_description"] == "Does a thing"
    assert detail["mcp_parameters_schema"]["type"] == "object"


async def test_workflow_mcp_tool_name_validated(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client)
    resp = await client.put(
        f"/workflows/{workflow_id}", json={"mcp_tool_name": "bad name!"}
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/api/tests/test_mcp_server.py -v`
Expected: FAIL — `mcp_enabled` missing from response / 200 instead of 400.

- [ ] **Step 3: Add columns to the `Workflow` model**

In `apps/api/app/models.py`, the imports already include `true` from sqlalchemy; extend that import with `false` (find `true()` usage; the import line is near the top — add `false` next to `true`). Then inside `class Workflow(Base)` after `run_timeout_seconds` add:

```python
    # MCP exposure (Phase A): when ``mcp_enabled`` the workflow is listed as a
    # callable tool on the /mcp server. ``mcp_tool_name`` overrides the
    # generated tool name; ``mcp_parameters_schema`` is the JSON Schema the
    # tool advertises for its arguments (null -> permissive object).
    mcp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false()
    )
    mcp_tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mcp_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mcp_parameters_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Write the migration**

```python
# apps/api/alembic/versions/0050_workflow_mcp.py
"""MCP server: per-workflow tool exposure columns.

``mcp_enabled`` opts the workflow into the /mcp tools list;
``mcp_tool_name`` / ``mcp_description`` / ``mcp_parameters_schema`` shape the
advertised tool. Additive and nullable/defaulted — no behaviour change while
off.

Revision ID: 0050_workflow_mcp
Revises: 0049_queue_trace_context
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_workflow_mcp"
down_revision: str | None = "0049_queue_trace_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "mcp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("workflows", sa.Column("mcp_tool_name", sa.String(64), nullable=True))
    op.add_column(
        "workflows", sa.Column("mcp_description", sa.String(500), nullable=True)
    )
    op.add_column(
        "workflows", sa.Column("mcp_parameters_schema", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflows", "mcp_parameters_schema")
    op.drop_column("workflows", "mcp_description")
    op.drop_column("workflows", "mcp_tool_name")
    op.drop_column("workflows", "mcp_enabled")
```

- [ ] **Step 5: Extend schemas**

In `apps/api/app/schemas.py`, find `class WorkflowUpdate` and add fields (match the optional-field style already used there):

```python
    mcp_enabled: bool | None = None
    mcp_tool_name: str | None = None
    mcp_description: str | None = None
    mcp_parameters_schema: dict | None = None
```

Find `class WorkflowDetail` and add:

```python
    mcp_enabled: bool = False
    mcp_tool_name: str | None = None
    mcp_description: str | None = None
    mcp_parameters_schema: dict | None = None
```

- [ ] **Step 6: Apply in the router**

In `apps/api/app/routers/workflows.py`:

In `_detail(...)`, add to the `WorkflowDetail(...)` constructor call:

```python
        mcp_enabled=workflow.mcp_enabled,
        mcp_tool_name=workflow.mcp_tool_name,
        mcp_description=workflow.mcp_description,
        mcp_parameters_schema=workflow.mcp_parameters_schema,
```

In `update_workflow(...)`, after the `run_timeout_seconds` block and before the `graph` block, add:

```python
    if body.mcp_enabled is not None:
        workflow.mcp_enabled = body.mcp_enabled
    if body.mcp_tool_name is not None:
        name_value = body.mcp_tool_name.strip()
        if name_value and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name_value):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "mcp_tool_name must match [A-Za-z0-9_-]{1,64}.",
            )
        workflow.mcp_tool_name = name_value or None
    if body.mcp_description is not None:
        workflow.mcp_description = body.mcp_description.strip() or None
    if body.mcp_parameters_schema is not None:
        workflow.mcp_parameters_schema = body.mcp_parameters_schema
```

Add `import re` at the top of `workflows.py`.

- [ ] **Step 7: Run tests**

Run: `python -m pytest apps/api/tests/test_mcp_server.py apps/api/tests/test_workflows.py -v`
(Note: tests create the schema from `Base.metadata`, so the new columns exist without running alembic; the migration is for real deployments.)
Expected: PASS (both new tests; no regressions in workflow tests)

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/models.py apps/api/alembic/versions/0050_workflow_mcp.py apps/api/app/schemas.py apps/api/app/routers/workflows.py apps/api/tests/test_mcp_server.py
git commit -m "feat(api): workflow mcp_* columns + schema/PUT support (migration 0050)"
```

---

### Task A3: MCP tool registry — read-only tools

**Files:**
- Create: `apps/api/app/mcp/tools.py`
- Test: `apps/api/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_mcp_tools.py
from app.mcp.tools import STATIC_TOOLS, get_tool


def test_static_registry_names_unique_and_complete() -> None:
    names = [t.name for t in STATIC_TOOLS]
    assert len(names) == len(set(names))
    for expected in (
        "list_workflows",
        "get_workflow",
        "run_workflow",
        "get_run",
        "list_node_types",
        "get_node_type",
        "create_workflow",
        "set_workflow_graph",
        "validate_graph",
        "publish_workflow",
    ):
        assert expected in names


def test_get_tool_lookup() -> None:
    assert get_tool("list_workflows") is not None
    assert get_tool("nope") is None


def test_every_tool_has_object_schema() -> None:
    for tool in STATIC_TOOLS:
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.description, str) and tool.description
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/api/tests/test_mcp_tools.py -v`
Expected: FAIL with `ImportError` (no `app.mcp.tools`)

- [ ] **Step 3: Create `tools.py` with the framework and read-only tools**

```python
# apps/api/app/mcp/tools.py
"""MCP tool registry: static builder/runner tools + per-workflow dynamic tools.

Each tool couples a JSON-Schema input contract with an async handler. The
``permission`` key maps into the existing RBAC table
(``app.security._PERMISSION_MIN_ROLE``); ``None`` means viewer-level access.
Handlers raise :class:`McpToolError` for anything the calling model should
read and recover from — the router renders it as an ``isError`` tool result.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import noodle_nodes  # noqa: F401 - registers built-in nodes
from app.db import SessionLocal
from app.models import NodeRun, Run, User, Workflow, WorkflowVersion
from app.services.audit import log_audit
from app.services.runner import start_run
from app.services.triggers import _await_run_terminal, _last_node_output
from noodle.models import WorkflowGraph
from noodle.sdk import registry as node_registry
from noodle_exporter import slugify

EMPTY_GRAPH: dict = {"nodes": [], "edges": []}
MAX_WAIT_SECONDS = 300.0
DEFAULT_WAIT_SECONDS = 60.0
OUTPUT_TRUNCATE_BYTES = 8000


class McpToolError(Exception):
    """Tool-level failure whose message goes back to the calling model."""


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict
    permission: str | None
    handler: Callable[[AsyncSession, User | None, dict], Awaitable[Any]]

    def descriptor(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _truncated(value: Any) -> Any:
    """Cap a node output for transport; large payloads become a text preview."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) <= OUTPUT_TRUNCATE_BYTES:
        return value
    return {
        "_truncated": True,
        "preview": encoded[:OUTPUT_TRUNCATE_BYTES],
        "total_chars": len(encoded),
    }


async def _load_workflow(session: AsyncSession, workflow_id: str) -> Workflow:
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    workflow = await session.get(
        Workflow, workflow_id, options=[selectinload(Workflow.versions)]
    )
    if workflow is None:
        raise McpToolError(f"Workflow not found: {workflow_id}")
    return workflow


def _draft_graph(workflow: Workflow) -> dict:
    if workflow.draft_graph is not None:
        return workflow.draft_graph
    if workflow.versions:
        return workflow.versions[-1].graph or EMPTY_GRAPH
    return EMPTY_GRAPH


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------


async def _list_workflows(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    limit = max(1, min(int(args.get("limit") or 50), 200))
    search = str(args.get("search") or "").strip().lower()
    rows = (
        await session.scalars(
            select(Workflow)
            .options(selectinload(Workflow.versions))
            .order_by(Workflow.updated_at.desc())
            .limit(500)
        )
    ).all()
    out: list[dict] = []
    for wf in rows:
        if search and search not in wf.name.lower():
            continue
        graph = _draft_graph(wf)
        out.append(
            {
                "id": wf.id,
                "name": wf.name,
                "active": wf.active,
                "published_version": wf.published_version,
                "node_count": len(graph.get("nodes", [])),
                "mcp_enabled": bool(wf.mcp_enabled),
            }
        )
        if len(out) >= limit:
            break
    return {"workflows": out}


async def _get_workflow(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    return {
        "id": workflow.id,
        "name": workflow.name,
        "active": workflow.active,
        "published_version": workflow.published_version,
        "graph": _draft_graph(workflow),
    }


async def _list_node_types(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    category = str(args.get("category") or "").strip()
    search = str(args.get("search") or "").strip().lower()
    out: list[dict] = []
    for manifest in node_registry.manifests():
        if manifest.hidden or manifest.deprecated:
            continue
        if category and manifest.category != category:
            continue
        if search and search not in f"{manifest.id} {manifest.name} {manifest.description}".lower():
            continue
        out.append(
            {
                "id": manifest.id,
                "name": manifest.name,
                "category": manifest.category,
                "description": manifest.description,
            }
        )
    return {"node_types": out, "total": len(out)}


async def _get_node_type(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    node_type = str(args.get("node_type") or "")
    for manifest in node_registry.manifests():
        if manifest.id == node_type:
            return manifest.model_dump(mode="json")
    raise McpToolError(
        f"Unknown node type: {node_type!r}. Use list_node_types to discover ids."
    )


async def _get_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "")
    if not run_id:
        raise McpToolError("run_id is required.")
    run = await session.get(Run, run_id, options=[selectinload(Run.node_runs)])
    if run is None:
        raise McpToolError(f"Run not found: {run_id}")
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "status": run.status,
        "started_at": str(run.started_at),
        "finished_at": str(run.finished_at) if run.finished_at else None,
        "nodes": [
            {
                "node_id": nr.node_id,
                "status": nr.status,
                "error": nr.error,
                "output": _truncated(nr.output),
            }
            for nr in run.node_runs
        ],
    }


# (run/build tools are appended in Tasks A4/A5)

STATIC_TOOLS: list[McpTool] = [
    McpTool(
        name="list_workflows",
        description=(
            "List Noodle workflows with id, name, active state and node count. "
            "Optionally filter by a case-insensitive name substring."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Name substring filter."},
                "limit": {"type": "integer", "description": "Max results (1-200, default 50)."},
            },
        },
        permission=None,
        handler=_list_workflows,
    ),
    McpTool(
        name="get_workflow",
        description="Fetch one workflow's metadata and current draft graph (nodes + edges).",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        permission=None,
        handler=_get_workflow,
    ),
    McpTool(
        name="list_node_types",
        description=(
            "List available node types (id, name, category, description) for building "
            "workflow graphs. Filter by category or search term to keep results small."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "search": {"type": "string"},
            },
        },
        permission=None,
        handler=_list_node_types,
    ),
    McpTool(
        name="get_node_type",
        description=(
            "Full manifest for one node type: parameters (names, types, choices, "
            "defaults, required), input/output ports. Call before placing a node."
        ),
        input_schema={
            "type": "object",
            "properties": {"node_type": {"type": "string"}},
            "required": ["node_type"],
        },
        permission=None,
        handler=_get_node_type,
    ),
    McpTool(
        name="get_run",
        description="Status and per-node outputs/errors for a run id (poll after run_workflow times out).",
        input_schema={
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
        permission=None,
        handler=_get_run,
    ),
]


def get_tool(name: str) -> McpTool | None:
    for tool in STATIC_TOOLS:
        if tool.name == name:
            return tool
    return None
```

- [ ] **Step 4: Adjust for actual model field names**

Read `class NodeRun` in `apps/api/app/models.py`. The handler above assumes fields `node_id`, `status`, `error`, `output`. If any name differs (e.g. `output` is `outputs`), fix `_get_run` accordingly. `_last_node_output` in `app/services/triggers.py` selects `NodeRun.output`, so `output` is almost certainly right.

- [ ] **Step 5: Run test — it still fails on missing names**

The registry test requires `run_workflow`, `create_workflow`, `set_workflow_graph`, `validate_graph`, `publish_workflow` — added in Tasks A4/A5. Temporarily verify only the lookup/schema tests:

Run: `python -m pytest apps/api/tests/test_mcp_tools.py::test_get_tool_lookup apps/api/tests/test_mcp_tools.py::test_every_tool_has_object_schema -v`
Expected: PASS (the completeness test stays red until A5)

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_tools.py
git commit -m "feat(api): MCP tool registry with read-only workflow/node/run tools"
```

---

### Task A4: `run_workflow` tool (start + wait + collect output)

**Files:**
- Modify: `apps/api/app/mcp/tools.py`

- [ ] **Step 1: Add the run handler and helper**

Insert after `_get_run` in `apps/api/app/mcp/tools.py`:

```python
# ---------------------------------------------------------------------------
# Run tools
# ---------------------------------------------------------------------------


async def _run_outcome(run_id: str, wait_seconds: float) -> dict:
    """Wait for terminal state and assemble the tool-facing result."""
    status = await _await_run_terminal(run_id, wait_seconds)
    if status is None:
        return {
            "run_id": run_id,
            "status": "running",
            "hint": "Run is still executing. Poll with get_run using this run_id.",
        }
    result: dict[str, Any] = {"run_id": run_id, "status": status}
    async with SessionLocal() as session:
        if status == "success":
            result["output"] = _truncated(await _last_node_output(session, run_id))
        else:
            rows = (
                await session.scalars(
                    select(NodeRun).where(
                        NodeRun.run_id == run_id, NodeRun.status == "error"
                    )
                )
            ).all()
            result["errors"] = [
                {"node_id": nr.node_id, "error": nr.error} for nr in rows
            ]
    return result


async def run_workflow_by_id(
    session: AsyncSession,
    workflow_id: str,
    *,
    parameters: dict | None,
    wait_seconds: float,
    use_draft: bool,
) -> dict:
    """Shared by the static run_workflow tool and dynamic per-workflow tools."""
    workflow = await _load_workflow(session, workflow_id)
    if not workflow.versions:
        raise McpToolError("Workflow has no versions.")
    latest = workflow.versions[-1]
    if use_draft:
        graph = _draft_graph(workflow)
        version_id = None
    else:
        graph = latest.graph or EMPTY_GRAPH
        version_id = latest.id
    try:
        run_id = await start_run(
            workflow_id,
            graph,
            latest.version,
            workflow_version_id=version_id,
            mode="manual",
            trigger_type="mcp",
            parameters=parameters or None,
        )
    except ValueError as exc:
        raise McpToolError(str(exc)) from exc
    except RuntimeError as exc:
        raise McpToolError(str(exc)) from exc
    return await _run_outcome(run_id, wait_seconds)


async def _run_workflow(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    parameters = args.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise McpToolError("parameters must be a JSON object.")
    try:
        wait_seconds = float(args.get("wait_seconds", DEFAULT_WAIT_SECONDS))
    except (TypeError, ValueError):
        wait_seconds = DEFAULT_WAIT_SECONDS
    wait_seconds = max(0.0, min(wait_seconds, MAX_WAIT_SECONDS))
    use_draft = bool(args.get("use_draft", True))
    return await run_workflow_by_id(
        session,
        str(args.get("workflow_id") or ""),
        parameters=parameters,
        wait_seconds=wait_seconds,
        use_draft=use_draft,
    )
```

Append to `STATIC_TOOLS`:

```python
    McpTool(
        name="run_workflow",
        description=(
            "Run a workflow and wait up to wait_seconds for it to finish. Returns "
            "{run_id, status, output} on completion, node errors on failure, or "
            "status='running' if still executing (then poll get_run). "
            "parameters seeds the workflow's trigger node."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "parameters": {
                    "type": "object",
                    "description": "Input payload delivered to the trigger node.",
                },
                "wait_seconds": {
                    "type": "number",
                    "description": "How long to wait for completion (0-300, default 60).",
                },
                "use_draft": {
                    "type": "boolean",
                    "description": "Run the draft graph (default true) or the published version.",
                },
            },
            "required": ["workflow_id"],
        },
        permission="workflow:run",
        handler=_run_workflow,
    ),
```

- [ ] **Step 2: Run the still-partial registry test**

Run: `python -m pytest apps/api/tests/test_mcp_tools.py -v`
Expected: completeness test still FAILS (missing build tools), the others PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/mcp/tools.py
git commit -m "feat(api): MCP run_workflow tool - start_run + terminal wait + output collection"
```

---

### Task A5: Builder tools (`create_workflow`, `set_workflow_graph`, `validate_graph`, `publish_workflow`) + dynamic workflow tools

**Files:**
- Modify: `apps/api/app/mcp/tools.py`

- [ ] **Step 1: Add graph validation helper and build handlers**

Insert after the run tools in `apps/api/app/mcp/tools.py`:

```python
# ---------------------------------------------------------------------------
# Builder tools
# ---------------------------------------------------------------------------


def _validate_graph_payload(graph: Any) -> WorkflowGraph:
    if not isinstance(graph, dict):
        raise McpToolError("graph must be an object: {\"nodes\": [...], \"edges\": [...]}")
    try:
        parsed = WorkflowGraph.model_validate(graph)
    except ValidationError as exc:
        raise McpToolError(f"Invalid graph: {exc.errors()[:5]}") from exc
    known = {m.id for m in node_registry.manifests()}
    unknown = sorted(
        {
            n.type
            for n in parsed.nodes
            if n.type and n.type not in known and not n.type.startswith("user:")
        }
    )
    if unknown:
        raise McpToolError(
            "Unknown node types: "
            + ", ".join(unknown)
            + ". Use list_node_types / get_node_type to discover valid ids."
        )
    return parsed


async def _create_workflow(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    name = str(args.get("name") or "").strip()
    if not name:
        raise McpToolError("name is required.")
    from app.routers.workflows import _global_env_id

    workflow = Workflow(
        name=name,
        environment_id=await _global_env_id(session),
        draft_graph=dict(EMPTY_GRAPH),
        published_version=1,
    )
    workflow.versions.append(WorkflowVersion(version=1, graph=dict(EMPTY_GRAPH)))
    session.add(workflow)
    await log_audit(
        session, "create", "workflow", detail=f"mcp: {name}",
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "name": name}


async def _set_workflow_graph(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    parsed = _validate_graph_payload(args.get("graph"))
    workflow.draft_graph = parsed.model_dump()
    await log_audit(
        session, "mcp_set_graph", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {
        "workflow_id": workflow.id,
        "node_count": len(parsed.nodes),
        "edge_count": len(parsed.edges),
        "hint": "Draft saved. Use run_workflow (use_draft=true) to test, then publish_workflow.",
    }


async def _validate_graph(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    parsed = _validate_graph_payload(args.get("graph"))
    return {"valid": True, "node_count": len(parsed.nodes), "edge_count": len(parsed.edges)}


async def _publish_workflow(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    from app.routers.workflows import publish_workflow as publish_route
    from app.schemas import WorkflowPublishRequest

    workflow_id = str(args.get("workflow_id") or "")
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    try:
        response = await publish_route(
            workflow_id,
            WorkflowPublishRequest(notes=str(args.get("notes") or "") or None),
            session,
            user,
        )
    except Exception as exc:  # HTTPException 404 etc. -> readable tool error
        raise McpToolError(f"Publish failed: {exc}") from exc
    return response.model_dump()
```

Append to `STATIC_TOOLS`:

```python
    McpTool(
        name="create_workflow",
        description="Create a new empty workflow and return its id.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        permission="workflow:write",
        handler=_create_workflow,
    ),
    McpTool(
        name="set_workflow_graph",
        description=(
            "Replace a workflow's draft graph. graph = {nodes: [{id, type, params, "
            "position?}], edges: [{source, source_output?, target, target_input?}]}. "
            "Node types must come from list_node_types; every workflow needs a "
            "trigger node (e.g. manual_trigger) to be runnable. Validation errors "
            "are returned as readable text — fix and retry."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "graph": {"type": "object"},
            },
            "required": ["workflow_id", "graph"],
        },
        permission="workflow:write",
        handler=_set_workflow_graph,
    ),
    McpTool(
        name="validate_graph",
        description="Validate a graph payload without saving it (shape + node types).",
        input_schema={
            "type": "object",
            "properties": {"graph": {"type": "object"}},
            "required": ["graph"],
        },
        permission=None,
        handler=_validate_graph,
    ),
    McpTool(
        name="publish_workflow",
        description="Publish the current draft as a new immutable version.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_publish_workflow,
    ),
```

- [ ] **Step 2: Add dynamic per-workflow tools at the end of the file**

```python
# ---------------------------------------------------------------------------
# Dynamic per-workflow tools (workflows with mcp_enabled=True)
# ---------------------------------------------------------------------------

_PERMISSIVE_SCHEMA: dict = {"type": "object", "properties": {}, "additionalProperties": True}


def workflow_tool_name(workflow: Workflow) -> str:
    if workflow.mcp_tool_name:
        return workflow.mcp_tool_name
    slug = slugify(workflow.name).replace("-", "_")
    return f"workflow_{slug}_{workflow.id[:6]}"


async def _mcp_enabled_workflows(session: AsyncSession) -> list[Workflow]:
    rows = await session.scalars(
        select(Workflow)
        .where(Workflow.mcp_enabled.is_(True))
        .options(selectinload(Workflow.versions))
        .order_by(Workflow.updated_at.desc())
    )
    return list(rows.all())


async def list_workflow_tool_descriptors(session: AsyncSession) -> list[dict]:
    out: list[dict] = []
    static_names = {t.name for t in STATIC_TOOLS}
    for wf in await _mcp_enabled_workflows(session):
        name = workflow_tool_name(wf)
        if name in static_names:
            continue  # a custom tool name may not shadow a built-in tool
        schema = wf.mcp_parameters_schema
        out.append(
            {
                "name": name,
                "description": wf.mcp_description or f"Run the Noodle workflow '{wf.name}'.",
                "inputSchema": schema if isinstance(schema, dict) and schema else _PERMISSIVE_SCHEMA,
            }
        )
    return out


async def call_workflow_tool(
    session: AsyncSession, user: User | None, name: str, arguments: dict
) -> Any | None:
    """Dispatch a dynamic workflow tool by name; None when no workflow matches."""
    for wf in await _mcp_enabled_workflows(session):
        if workflow_tool_name(wf) == name:
            return await run_workflow_by_id(
                session,
                wf.id,
                parameters=arguments or None,
                wait_seconds=DEFAULT_WAIT_SECONDS,
                use_draft=False,
            )
    return None
```

- [ ] **Step 3: Run the full tools test**

Run: `python -m pytest apps/api/tests/test_mcp_tools.py -v`
Expected: 3 PASS (registry now complete)

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/mcp/tools.py
git commit -m "feat(api): MCP builder tools + dynamic per-workflow tools"
```

---

### Task A6: `/mcp` router + config flag + wiring

**Files:**
- Create: `apps/api/app/routers/mcp.py`
- Modify: `apps/api/app/config.py` (add `mcp_server_enabled` near the `otel_enabled` flags, ~line 184)
- Modify: `apps/api/app/main.py` (import + conditional `include_router`)

- [ ] **Step 1: Write the router**

```python
# apps/api/app/routers/mcp.py
"""MCP server endpoint (streamable-HTTP transport, stateless JSON mode).

A single JSON-RPC 2.0 message per POST; responses are plain JSON (the MCP
spec allows servers to answer with ``application/json`` instead of an SSE
stream and to operate sessionless). GET/DELETE are 405 because this server
never opens server-initiated streams.

Auth mirrors the rest of the API: optional bearer session token, required
when ``settings.auth_required``. Tool-level failures come back as MCP tool
results with ``isError`` so the calling model can self-correct; transport
auth failures are HTTP 401.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    initialize_result,
    jsonrpc_error,
    jsonrpc_result,
    tool_result,
)
from app.mcp.tools import (
    STATIC_TOOLS,
    McpToolError,
    call_workflow_tool,
    get_tool,
    list_workflow_tool_descriptors,
)
from app.models import User
from app.security import _PERMISSION_MIN_ROLE, _role_for, current_user, role_allows
from app.tenancy import current_org_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])


@router.get("/mcp")
async def mcp_get() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
async def mcp_delete() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


async def _check_permission(
    session: AsyncSession, user: User | None, permission: str | None
) -> None:
    """RBAC for one tool call. Raises McpToolError on denial."""
    if permission is None:
        return
    minimum = _PERMISSION_MIN_ROLE[permission]
    if user is None:
        if settings.auth_required:
            raise McpToolError("Authentication required for this tool.")
        return
    org_id = current_org_id.get() if settings.multi_tenancy_enabled else None
    role = await _role_for(session, user, org_id)
    if not role_allows(role, minimum):
        raise McpToolError(f"This tool requires the {minimum} role or higher.")


@router.post("/mcp")
async def mcp_post(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    # --- transport-level auth ---
    user: User | None = None
    if authorization:
        try:
            user = await current_user(authorization=authorization, session=session)
        except HTTPException:
            return Response(
                status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
    elif settings.auth_required:
        return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})

    # --- parse ---
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(jsonrpc_error(None, PARSE_ERROR, "Invalid JSON."))
    if isinstance(body, list):
        return JSONResponse(
            jsonrpc_error(None, INVALID_REQUEST, "Batch requests are not supported.")
        )
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        return JSONResponse(
            jsonrpc_error(None, INVALID_REQUEST, "Expected a JSON-RPC request object.")
        )

    method = body["method"]
    params = body.get("params") if isinstance(body.get("params"), dict) else {}

    # Notifications (no id) are acknowledged and ignored.
    if "id" not in body:
        return Response(status_code=202)
    req_id = body.get("id")

    if method == "initialize":
        return JSONResponse(
            jsonrpc_result(req_id, initialize_result(params.get("protocolVersion")))
        )
    if method == "ping":
        return JSONResponse(jsonrpc_result(req_id, {}))
    if method == "tools/list":
        tools = [tool.descriptor() for tool in STATIC_TOOLS]
        tools.extend(await list_workflow_tool_descriptors(session))
        return JSONResponse(jsonrpc_result(req_id, {"tools": tools}))
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        try:
            tool = get_tool(name)
            if tool is not None:
                await _check_permission(session, user, tool.permission)
                payload = await tool.handler(session, user, arguments)
                return JSONResponse(jsonrpc_result(req_id, tool_result(payload)))
            # Dynamic per-workflow tool — running a workflow needs workflow:run.
            await _check_permission(session, user, "workflow:run")
            payload = await call_workflow_tool(session, user, name, arguments)
            if payload is not None:
                return JSONResponse(jsonrpc_result(req_id, tool_result(payload)))
            return JSONResponse(
                jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown tool: {name}")
            )
        except McpToolError as exc:
            return JSONResponse(
                jsonrpc_result(req_id, tool_result(str(exc), is_error=True))
            )
        except Exception as exc:  # noqa: BLE001 - tool failures go to the model
            logger.exception("mcp tool %s failed", name)
            return JSONResponse(
                jsonrpc_result(
                    req_id,
                    tool_result(f"{type(exc).__name__}: {exc}", is_error=True),
                )
            )

    return JSONResponse(
        jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown method: {method}")
    )
```

- [ ] **Step 2: Config flag**

In `apps/api/app/config.py`, next to `otel_enabled` (~line 184) add:

```python
    # MCP server: exposes POST /mcp (workflow run + builder tools) when on.
    mcp_server_enabled: bool = True
```

- [ ] **Step 3: Wire into the app**

In `apps/api/app/main.py`:
1. Add `mcp` to the `from app.routers import (...)` list (alphabetical — after `internal`, before `nodes`).
2. Find the block of `app.include_router(...)` calls and add, following the conditional style used for webhook routers:

```python
if settings.mcp_server_enabled:
    app.include_router(mcp.router)
```

- [ ] **Step 4: Smoke check the app imports**

Run: `python -c "import sys; sys.path.insert(0, 'apps/api'); from app.main import app; print([r.path for r in app.routes if 'mcp' in r.path])"`
Expected: prints `['/mcp', '/mcp', '/mcp']` (GET, DELETE, POST). If the import style differs, run from `apps/api` instead.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/mcp.py apps/api/app/config.py apps/api/app/main.py
git commit -m "feat(api): /mcp endpoint - stateless streamable-HTTP JSON-RPC router + mcp_server_enabled flag"
```

---

### Task A7: End-to-end MCP server tests

**Files:**
- Modify: `apps/api/tests/test_mcp_server.py` (append)

- [ ] **Step 1: Append the protocol/e2e tests**

```python
import json


async def test_initialize(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        ),
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "noodle"
    assert "tools" in result["capabilities"]


async def test_notification_returns_202(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert resp.status_code == 202


async def test_get_is_405(client: AsyncClient) -> None:
    assert (await client.get("/mcp")).status_code == 405


async def test_batch_rejected(client: AsyncClient) -> None:
    resp = await client.post("/mcp", json=[rpc("ping")])
    assert resp.json()["error"]["code"] == -32600


async def test_tools_list_contains_static_tools(client: AsyncClient) -> None:
    resp = await client.post("/mcp", json=rpc("tools/list"))
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert {"run_workflow", "set_workflow_graph", "list_node_types"} <= names


def _tool_payload(resp) -> dict:
    """Parse a tools/call response's text content as JSON."""
    result = resp.json()["result"]
    assert result["isError"] is False, result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


async def test_build_and_run_workflow_via_mcp(client: AsyncClient) -> None:
    created = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "create_workflow", "arguments": {"name": "Via MCP"}}),
        )
    )
    workflow_id = created["workflow_id"]

    set_resp = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {
                    "name": "set_workflow_graph",
                    "arguments": {"workflow_id": workflow_id, "graph": MANUAL_GRAPH},
                },
            ),
        )
    )
    assert set_resp["node_count"] == 1

    run = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}},
            ),
        )
    )
    assert run["status"] == "success"
    assert "run_id" in run


async def test_set_graph_rejects_unknown_node_type(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Bad Graph WF")
    resp = await client.post(
        "/mcp",
        json=rpc(
            "tools/call",
            {
                "name": "set_workflow_graph",
                "arguments": {
                    "workflow_id": workflow_id,
                    "graph": {
                        "nodes": [{"id": "x", "type": "no_such_node", "params": {}}],
                        "edges": [],
                    },
                },
            },
        ),
    )
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "Unknown node types" in result["content"][0]["text"]


async def test_workflow_exposed_as_dynamic_tool(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Dyn Tool WF")
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    await client.put(
        f"/workflows/{workflow_id}",
        json={"mcp_enabled": True, "mcp_tool_name": "dyn_tool_wf"},
    )

    listing = await client.post("/mcp", json=rpc("tools/list"))
    names = {t["name"] for t in listing.json()["result"]["tools"]}
    assert "dyn_tool_wf" in names

    run = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "dyn_tool_wf", "arguments": {}}),
        )
    )
    assert run["status"] == "success"


async def test_unknown_tool_is_method_not_found(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp", json=rpc("tools/call", {"name": "nope_tool", "arguments": {}})
    )
    assert resp.json()["error"]["code"] == -32601
```

- [ ] **Step 2: Run the whole file**

Run: `python -m pytest apps/api/tests/test_mcp_server.py -v`
Expected: all PASS. (Tests run with `run_synchronously=True`, so `start_run` completes before returning and the wait helper resolves immediately.)

- [ ] **Step 3: Run the broader API suite to catch regressions**

Run: `python -m pytest apps/api/tests -x -q`
Expected: PASS (no regressions; if unrelated pre-existing failures occur, confirm they fail on the base branch too before proceeding).

- [ ] **Step 4: Commit**

```bash
git add apps/api/tests/test_mcp_server.py
git commit -m "test(api): MCP server end-to-end - initialize, build+run via tools, dynamic workflow tools"
```

---

### Task A8: Web UI — workflow MCP settings + docs

**Files:**
- Modify: `apps/web/src/api.ts` (WorkflowDetail/update types, near `run_timeout_seconds` at line ~208)
- Modify: `apps/web/src/types.ts` (same fields, near line ~229)
- Modify: `apps/web/src/EditorPage.tsx` (workflow settings panel — the component that already edits `run_timeout_seconds`, see lines ~313 and ~518)
- Create: `docs/mcp.md`

- [ ] **Step 1: Extend the TS types**

In both `apps/web/src/api.ts` and `apps/web/src/types.ts`, on the workflow detail/update interfaces that already carry `run_timeout_seconds?: number | null;`, add:

```typescript
  mcp_enabled?: boolean;
  mcp_tool_name?: string | null;
  mcp_description?: string | null;
  mcp_parameters_schema?: Record<string, unknown> | null;
```

- [ ] **Step 2: Add the settings UI**

In `apps/web/src/EditorPage.tsx`, locate the workflow-settings state initialisation (~line 313, where `run_timeout_seconds` is read into state) and the two save payloads (~lines 518 and 679). Mirror the existing pattern:

1. Add state: `const [mcpEnabled, setMcpEnabled] = useState(false);` and `const [mcpToolName, setMcpToolName] = useState("");` plus `const [mcpDescription, setMcpDescription] = useState("");`
2. Initialise from `detail.mcp_enabled ?? false`, `detail.mcp_tool_name ?? ""`, `detail.mcp_description ?? ""` next to the `run_timeout_seconds` initialisation.
3. Add to both save payloads: `mcp_enabled: mcpEnabled, mcp_tool_name: mcpToolName || null, mcp_description: mcpDescription || null,`
4. In the settings panel JSX (same section that renders the run-timeout input), append:

```tsx
          <label className="settings-row">
            <input
              type="checkbox"
              checked={mcpEnabled}
              onChange={(e) => setMcpEnabled(e.target.checked)}
            />
            Expose as MCP tool (AI agents can call this workflow via /mcp)
          </label>
          {mcpEnabled && (
            <>
              <label className="settings-row">
                Tool name
                <input
                  value={mcpToolName}
                  placeholder="auto-generated from workflow name"
                  onChange={(e) => setMcpToolName(e.target.value)}
                />
              </label>
              <label className="settings-row">
                Tool description
                <input
                  value={mcpDescription}
                  placeholder="What this workflow does, for the calling model"
                  onChange={(e) => setMcpDescription(e.target.value)}
                />
              </label>
            </>
          )}
```

Match the surrounding class names/markup — if the panel uses different wrappers than `label.settings-row`, copy the exact structure of the run-timeout field instead.

- [ ] **Step 3: Write `docs/mcp.md`**

```markdown
# MCP support

## Noodle as an MCP server

Noodle exposes an MCP server at `POST /mcp` (streamable HTTP, stateless).
Disable with `MCP_SERVER_ENABLED=false`.

Connect from Claude Code:

    claude mcp add --transport http noodle http://localhost:8000/mcp \
      --header "Authorization: Bearer <session token>"

When `AUTH_REQUIRED=false` (local dev) the header may be omitted.
Run tools need a token whose role allows `workflow:run` (editor+);
builder tools need `workflow:write` (editor+).

### Tools

| Tool | Purpose |
|------|---------|
| `list_workflows` / `get_workflow` | discover workflows |
| `run_workflow` | run + wait, returns last-node output |
| `get_run` | poll a still-running run |
| `list_node_types` / `get_node_type` | discover node palette |
| `create_workflow` / `set_workflow_graph` / `validate_graph` / `publish_workflow` | build workflows |

Workflows with **Expose as MCP tool** enabled in their settings additionally
appear as their own tools (published version runs).

## Noodle as an MCP client

Create an **MCP Server** credential (URL + optional bearer token), then use:

- **MCP Tools** (`mcp_tools`) — supplies the server's tools to an AI Agent's
  tools port. Side-effecting by default, so agent approval gating applies.
- **MCP Call Tool** (`mcp_call_tool`) — call one named tool in the data flow.
- **MCP List Tools** (`mcp_list_tools`) — inspect a server's tool list.

Only HTTP(S) MCP servers are supported (no stdio). URLs are SSRF-guarded.
```

- [ ] **Step 4: Type-check / build the web app**

Run: `npm --prefix apps/web run build` (or the repo's existing check command, see `apps/web/package.json` scripts)
Expected: compiles with no TS errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/api.ts apps/web/src/types.ts apps/web/src/EditorPage.tsx docs/mcp.md
git commit -m "feat(web): workflow MCP exposure settings + MCP docs"
```

---

# Phase B — MCP Client (nodes)

### Task B1: Dependency + credential type

**Files:**
- Modify: `packages/nodes/pyproject.toml`
- Modify: `apps/api/app/services/credential_types.py`

- [ ] **Step 1: Add the SDK dependency**

In `packages/nodes/pyproject.toml`, add to `dependencies`:

```toml
    "mcp>=1.9",
```

Then sync the environment: `python -m pip install -e packages/nodes` (or the repo's usual `uv sync` if uv-managed — check how `.venv` was built; use the same mechanism).

- [ ] **Step 2: Register the credential type**

In `apps/api/app/services/credential_types.py`, append to the `_TYPES` tuple (mirror neighbouring entries):

```python
    CredentialTypeSpec(
        id="mcp_server",
        name="MCP Server",
        provider="MCP",
        auth_method="api_key",
        fields=[
            CredentialFieldSpec(
                key="url",
                label="Server URL",
                secret=False,
                placeholder="https://example.com/mcp",
                help="Streamable-HTTP MCP endpoint.",
            ),
            CredentialFieldSpec(
                key="auth_token",
                label="Bearer token",
                required=False,
                placeholder="Optional Authorization bearer token",
            ),
            CredentialFieldSpec(
                key="headers_json",
                label="Extra headers (JSON object)",
                required=False,
                placeholder='{"X-Custom": "value"}',
            ),
        ],
        documentation_url="https://modelcontextprotocol.io",
    ),
```

(No `test_service` in v1 — credential test-on-save is skipped for this type.)

- [ ] **Step 3: Verify**

Run: `python -m pytest apps/api/tests -k credential -q`
Expected: PASS (existing credential-type listing tests tolerate the new entry; if a test asserts an exact type list, add `mcp_server` to it).

- [ ] **Step 4: Commit**

```bash
git add packages/nodes/pyproject.toml apps/api/app/services/credential_types.py
git commit -m "feat(nodes): mcp SDK dependency + mcp_server credential type"
```

---

### Task B2: MCP client nodes (`mcp_tools`, `mcp_call_tool`, `mcp_list_tools`)

**Files:**
- Create: `packages/nodes/noodle_nodes/ai_v2/mcp.py`
- Modify: `packages/nodes/noodle_nodes/ai_v2/__init__.py` (import the new module so its nodes register — copy the existing import style for `tools`/`agents`)
- Test: `packages/nodes/tests/test_ai_v2_mcp.py`

- [ ] **Step 1: Write the failing tests**

```python
# packages/nodes/tests/test_ai_v2_mcp.py
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from noodle.ai_runtime import ToolAdapter, ToolSchema
from noodle_nodes.ai_v2 import mcp as mcp_module
from noodle_nodes.ai_v2.mcp import (
    McpToolAdapter,
    _config_from_credentials,
    mcp_call_tool,
    mcp_list_tools,
    mcp_tools,
)


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="echo",
                    description="Echo text back",
                    inputSchema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),
                SimpleNamespace(name="other", description=None, inputSchema=None),
            ]
        )

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name == "structured":
            return SimpleNamespace(
                content=[], structuredContent={"ok": True}, isError=False
            )
        if name == "boom":
            return SimpleNamespace(
                content=[SimpleNamespace(text="it broke")],
                structuredContent=None,
                isError=True,
            )
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"echo:{arguments.get('text', '')}")],
            structuredContent=None,
            isError=False,
        )


@pytest.fixture
def fake_transport(monkeypatch):
    session = FakeSession()

    @asynccontextmanager
    async def _fake(config):
        yield session

    monkeypatch.setattr(mcp_module, "_mcp_session", _fake)
    return session


CREDS = {"url": "https://example.com/mcp", "auth_token": "tok"}


def test_config_from_credentials_builds_headers() -> None:
    config = _config_from_credentials(
        {"url": "https://x/mcp", "auth_token": "abc", "headers_json": '{"X-A": "1"}'}
    )
    assert config.url == "https://x/mcp"
    assert config.headers["Authorization"] == "Bearer abc"
    assert config.headers["X-A"] == "1"


def test_config_requires_url() -> None:
    with pytest.raises(ValueError):
        _config_from_credentials({"auth_token": "abc"})


async def test_mcp_tools_returns_adapters(fake_transport) -> None:
    adapters = await mcp_tools(credentials=CREDS)
    assert len(adapters) == 2
    assert all(isinstance(a, ToolAdapter) for a in adapters)
    echo = next(a for a in adapters if a.schema.name == "echo")
    assert echo.side_effecting is True
    assert echo.schema.parameters.required == ["text"]


async def test_mcp_tools_filter(fake_transport) -> None:
    adapters = await mcp_tools(credentials=CREDS, tool_filter="echo")
    assert [a.schema.name for a in adapters] == ["echo"]


async def test_adapter_invoke_async(fake_transport) -> None:
    adapters = await mcp_tools(credentials=CREDS)
    echo = next(a for a in adapters if a.schema.name == "echo")
    assert await echo.invoke_async({"text": "hi"}) == "echo:hi"


def test_adapter_sync_invoke_raises() -> None:
    adapter = McpToolAdapter(
        config=_config_from_credentials(CREDS),
        schema=ToolSchema(name="echo", description="d"),
        side_effecting=True,
    )
    with pytest.raises(RuntimeError):
        adapter.invoke({})


async def test_call_tool_node_parses_json(fake_transport) -> None:
    result = await mcp_call_tool(
        credentials=CREDS, tool_name="structured", arguments={"a": 1}
    )
    assert result == {"ok": True}
    assert fake_transport.calls == [("structured", {"a": 1})]


async def test_call_tool_node_error_raises(fake_transport) -> None:
    with pytest.raises(RuntimeError, match="it broke"):
        await mcp_call_tool(credentials=CREDS, tool_name="boom")


async def test_call_tool_accepts_json_string_arguments(fake_transport) -> None:
    await mcp_call_tool(
        credentials=CREDS, tool_name="echo", arguments=json.dumps({"text": "x"})
    )
    assert fake_transport.calls[-1] == ("echo", {"text": "x"})


async def test_list_tools_node(fake_transport) -> None:
    listing = await mcp_list_tools(credentials=CREDS)
    assert listing[0]["name"] == "echo"
    assert listing[0]["input_schema"]["type"] == "object"
```

Check `packages/nodes/tests/` for an existing `conftest.py` / pytest-asyncio configuration (other async node tests exist, e.g. `test_ai_v2_nodes.py`); match its idiom (`asyncio_mode = auto` vs `@pytest.mark.asyncio` decorators) and decorate the async tests accordingly if needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest packages/nodes/tests/test_ai_v2_mcp.py -v`
Expected: FAIL with `ImportError` (no `noodle_nodes.ai_v2.mcp`)

- [ ] **Step 3: Implement the module**

```python
# packages/nodes/noodle_nodes/ai_v2/mcp.py
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
from contextlib import asynccontextmanager
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
            pass  # malformed extra headers are ignored, not fatal
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


def _result_to_text(result: Any) -> str:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, default=str)
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


class McpToolAdapter(ToolAdapter):
    """Calls one remote MCP tool; a fresh session per invocation."""

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

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        async with _mcp_session(self._config) as session:
            result = await session.call_tool(self._schema.name, dict(arguments or {}))
        text = _result_to_text(result)
        if getattr(result, "isError", False):
            raise RuntimeError(text or f"{self._schema.name}: tool returned an error")
        return text


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
    if not str(tool_name or "").strip():
        raise ValueError("mcp_call_tool: tool_name is required")
    config = _config_from_credentials(credentials)
    args = _parse_arguments(arguments)
    if not args and isinstance(input, dict):
        args = input  # convenience: wired upstream object becomes the arguments
    async with _mcp_session(config) as session:
        result = await session.call_tool(str(tool_name).strip(), args)
    text = _result_to_text(result)
    if getattr(result, "isError", False):
        raise RuntimeError(text or f"mcp_call_tool: {tool_name} returned an error")
    try:
        return json.loads(text)
    except ValueError:
        return text


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
```

Then open `packages/nodes/noodle_nodes/ai_v2/__init__.py`, find where sibling modules (`tools`, `agents`, ...) are imported to register their nodes, and add `mcp` to that import in the same style.

- [ ] **Step 4: Run tests**

Run: `python -m pytest packages/nodes/tests/test_ai_v2_mcp.py -v`
Expected: all PASS

- [ ] **Step 5: Run the wider node + engine suites**

Run: `python -m pytest packages/nodes/tests -q && python -m pytest packages/core/tests -q`
Expected: PASS (registration of three new nodes must not break manifest-count or palette tests; if a test asserts an exact node count, update it).

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/mcp.py packages/nodes/noodle_nodes/ai_v2/__init__.py packages/nodes/tests/test_ai_v2_mcp.py
git commit -m "feat(nodes): MCP client - mcp_tools supplier, mcp_call_tool, mcp_list_tools"
```

---

### Task B3: Loopback integration test (Noodle client → Noodle server)

**Files:**
- Modify: `apps/api/tests/test_mcp_server.py` (append)

This proves the two halves speak the same protocol without any external server: the client-node helpers are pointed at the API's own `/mcp` endpoint via a monkeypatched transport that posts JSON-RPC through the test ASGI client.

- [ ] **Step 1: Append the loopback test**

```python
from contextlib import asynccontextmanager


class _LoopbackSession:
    """Minimal MCP client session that speaks to the test app's /mcp route."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._seq = 0

    async def _post(self, method: str, params: dict) -> dict:
        self._seq += 1
        resp = await self._client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": self._seq, "method": method, "params": params},
        )
        data = resp.json()
        assert "error" not in data, data
        return data["result"]

    async def list_tools(self):
        from types import SimpleNamespace

        result = await self._post("tools/list", {})
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name=t["name"],
                    description=t.get("description"),
                    inputSchema=t.get("inputSchema"),
                )
                for t in result["tools"]
            ]
        )

    async def call_tool(self, name: str, arguments: dict):
        from types import SimpleNamespace

        result = await self._post(
            "tools/call", {"name": name, "arguments": arguments}
        )
        return SimpleNamespace(
            content=[SimpleNamespace(text=c["text"]) for c in result["content"]],
            structuredContent=result.get("structuredContent"),
            isError=result["isError"],
        )


async def test_client_nodes_loopback_against_own_server(
    client: AsyncClient, monkeypatch
) -> None:
    from noodle_nodes.ai_v2 import mcp as mcp_module

    @asynccontextmanager
    async def _loopback(config):
        yield _LoopbackSession(client)

    monkeypatch.setattr(mcp_module, "_mcp_session", _loopback)

    creds = {"url": "https://loopback.invalid/mcp"}
    tools = await mcp_module.mcp_list_tools(credentials=creds)
    assert any(t["name"] == "list_workflows" for t in tools)

    workflow_id = await make_workflow(client, "Loopback WF")
    out = await mcp_module.mcp_call_tool(
        credentials=creds,
        tool_name="run_workflow",
        arguments={"workflow_id": workflow_id},
    )
    assert out["status"] == "success"
```

- [ ] **Step 2: Run**

Run: `python -m pytest apps/api/tests/test_mcp_server.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/test_mcp_server.py
git commit -m "test(api): loopback test - MCP client nodes against Noodle's own /mcp server"
```

---

# Phase C — Code-First Module Export

**Design recap (the contract the code below implements):**

The generated file contains, in order: docstring; imports; `workflow_registry = NodeRegistry()`; `_call_builtin` helpers; one `@node(..., registry=workflow_registry)` function per exportable graph node (defaults = configured params, `wires` = incoming edges, body delegates to the built-in implementation); module-level data (`_DELEGATES`, `_NODE_SETTINGS`, `_PARAM_OVERRIDES`, `_EXTRA_NODES`, `_EXTRA_EDGES`, `SUBWORKFLOWS`, `ROOT_ID`); `_build_graph()` (reads params back off the function signatures with `inspect`); `_runtime_registry()` (built-ins + hand-added custom nodes); `_seed_trigger`/`_run_subworkflow`/`main()` (same engine entry as the existing script export).

Per-node emission rules:
- node type known to the registry → decorated function. Function name = sanitized unique identifier; decorator `id` = the **original graph node id** (wires reference original ids, no mapping needed).
- node type unknown (`user:` code modules, anything unregistered) → raw dict in `_EXTRA_NODES` (same runtime behaviour as the existing JSON export).
- params: required manifest params (manifest default) overlaid with the instance's configured params. Param names that are not valid Python identifiers or are keywords go to `_PARAM_OVERRIDES` instead of the signature.
- edges: first edge per `(target, target_input)` becomes a `wires` entry; duplicates and edges into `_EXTRA_NODES` go to `_EXTRA_EDGES`.
- instance settings that differ from `GraphNode` defaults (position, on_error, retries, tool_mode, ...) go to `_NODE_SETTINGS[node_id]`.
- async built-ins get `async def` wrappers awaiting `_call_builtin_async`.

### Task C1: `module_codegen.py`

**Files:**
- Create: `packages/exporter/noodle_exporter/module_codegen.py`
- Modify: `packages/exporter/noodle_exporter/__init__.py` (export `workflow_to_module`)
- Test: `packages/exporter/tests/test_module_codegen.py` (create `packages/exporter/tests/__init__.py` only if sibling packages' test dirs have one — check `packages/core/tests` and mirror)

- [ ] **Step 1: Write the failing tests**

```python
# packages/exporter/tests/test_module_codegen.py
import noodle_nodes  # noqa: F401 - registers the built-in nodes
from noodle.models import WorkflowGraph
from noodle.sdk import registry
from noodle_exporter.module_codegen import workflow_to_module

GRAPH = WorkflowGraph.model_validate(
    {
        "nodes": [
            {"id": "start", "type": "manual_trigger", "params": {}},
            {
                "id": "fetch",
                "type": "http_request",
                "params": {"url": "https://example.com", "method": "GET"},
                "position": {"x": 300, "y": 120},
                "on_error": "continue",
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "fetch", "target_input": "input"}
        ],
    }
)


def _load_module(source: str) -> dict:
    module_globals: dict = {}
    exec(compile(source, "exported_wf.py", "exec"), module_globals)  # noqa: S102
    return module_globals


def test_module_compiles() -> None:
    source = workflow_to_module(GRAPH, "Demo Flow", registry=registry)
    compile(source, "exported_wf.py", "exec")
    assert "@node(" in source
    assert "def main()" in source
    assert "GRAPH =" not in source  # no embedded JSON graph


def test_params_become_function_defaults() -> None:
    source = workflow_to_module(GRAPH, "Demo Flow", registry=registry)
    assert "url='https://example.com'" in source or 'url="https://example.com"' in source


def test_build_graph_roundtrip() -> None:
    module = _load_module(workflow_to_module(GRAPH, "Demo Flow", registry=registry))
    graph = module["_build_graph"]()
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["start"]["type"] == "manual_trigger"
    assert by_id["fetch"]["type"] == "http_request"
    assert by_id["fetch"]["params"]["url"] == "https://example.com"
    assert by_id["fetch"]["on_error"] == "continue"
    assert any(
        e["source"] == "start"
        and e["target"] == "fetch"
        and e["target_input"] == "input"
        for e in graph["edges"]
    )
    parsed = WorkflowGraph.model_validate(graph)
    assert len(parsed.nodes) == 2


def test_editing_a_default_changes_the_built_graph() -> None:
    source = workflow_to_module(GRAPH, "Demo Flow", registry=registry)
    edited = source.replace("https://example.com", "https://edited.example")
    module = _load_module(edited)
    graph = module["_build_graph"]()
    fetch = next(n for n in graph["nodes"] if n["id"] == "fetch")
    assert fetch["params"]["url"] == "https://edited.example"


def test_unknown_node_types_fall_back_to_extra_nodes() -> None:
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {"id": "start", "type": "manual_trigger", "params": {}},
                {"id": "custom", "type": "user:abc123:my_fn", "params": {"x": 1}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "custom", "target_input": "input"}
            ],
        }
    )
    module = _load_module(workflow_to_module(graph, "Custom", registry=registry))
    built = module["_build_graph"]()
    types = {n["id"]: n["type"] for n in built["nodes"]}
    assert types["custom"] == "user:abc123:my_fn"
    assert any(e["target"] == "custom" for e in built["edges"])


def test_main_runs_trigger_only_graph(capsys) -> None:
    graph = WorkflowGraph.model_validate(
        {"nodes": [{"id": "t", "type": "manual_trigger", "params": {}}], "edges": []}
    )
    module = _load_module(workflow_to_module(graph, "Trigger Only", registry=registry))
    module["main"]()
    captured = capsys.readouterr()
    assert "success" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest packages/exporter/tests/test_module_codegen.py -v`
Expected: FAIL with `ImportError` (no `module_codegen`)

- [ ] **Step 3: Implement `module_codegen.py`**

```python
# packages/exporter/noodle_exporter/module_codegen.py
"""Generate a code-first Python module from a workflow graph (export v2).

Unlike :func:`noodle_exporter.codegen.workflow_to_script` (which embeds the
graph as JSON), this emits one ``@node``-decorated function per workflow
node. The function's keyword defaults are the node's configured params,
``wires={...}`` declares its incoming edges, and the body delegates to the
built-in implementation so each node can be called standalone. ``main()``
reassembles the ``WorkflowGraph`` from the functions and runs it through the
Noodle engine — execution semantics are identical to running inside Noodle.
"""

import json
import keyword
import re

from noodle.models import GraphNode, WorkflowGraph

_DEFAULT_NODE_FIELDS = GraphNode(id="_", type="_").model_dump(exclude={"id", "type", "params"})

_HEADER_TEMPLATE = '''"""Noodle workflow: __NAME__ (code-first export)

Generated by Noodle. Run with:  python __SLUG__.py
Requires: noodle-core and noodle-nodes (plus your environment packages).

Each workflow node is a decorated function below.

* Editing a function's KEYWORD DEFAULTS changes the node's configuration
  (main() reads params back off the signatures).
* Editing ``wires={...}`` changes the graph's edges.
* Editing a generated function's BODY does NOT change the engine-driven run
  for these delegate wrappers — the engine executes the original built-in
  node type (see _DELEGATES). The body exists so you can call the node
  directly, e.g. ``fetch(url="https://...")``, for testing.
* Adding a NEW @node function with a fresh id (and not adding it to
  _DELEGATES) registers a real custom node the engine executes directly;
  wire it in via its ``wires={...}``.
"""

import inspect

import noodle_nodes  # noqa: F401 - registers the built-in nodes
from noodle.engine import run
from noodle.engine.subworkflows import InlineSubworkflow, SubworkflowMeta
from noodle.models import WorkflowGraph
from noodle.sdk import NodeRegistry, node
from noodle.sdk import registry as builtin_registry

workflow_registry = NodeRegistry()


def _filtered_kwargs(node_type, kwargs):
    nd = builtin_registry.get(node_type)
    if not nd.accepts_var_keyword and nd.param_names:
        return {k: v for k, v in kwargs.items() if k in nd.param_names}
    return dict(kwargs)


def _call_builtin(node_type, **kwargs):
    return builtin_registry.get(node_type).func(**_filtered_kwargs(node_type, kwargs))


async def _call_builtin_async(node_type, **kwargs):
    return await builtin_registry.get(node_type).func(
        **_filtered_kwargs(node_type, kwargs)
    )
'''

_FOOTER_TEMPLATE = '''

_DELEGATES = __DELEGATES__

_NODE_SETTINGS = __NODE_SETTINGS__

_PARAM_OVERRIDES = __PARAM_OVERRIDES__

_EXTRA_NODES = __EXTRA_NODES__

_EXTRA_EDGES = __EXTRA_EDGES__

# Sub-workflows referenced by execute_workflow / map_* nodes, bundled at
# export time and resolved locally — no Noodle server required.
SUBWORKFLOWS = __SUBWORKFLOWS__

ROOT_ID = __ROOT_ID__

_TRIGGER_TYPES = (
    "manual_trigger", "webhook_trigger", "api_endpoint",
    "schedule_trigger", "chat_trigger",
)


def _build_graph():
    """Reassemble the workflow graph from the decorated functions above."""
    nodes, edges, seq = [], [], 0
    for nd in workflow_registry._nodes.values():  # noqa: SLF001
        input_ports = {p.name for p in nd.manifest.inputs}
        params = {}
        for pname, p in inspect.signature(nd.func).parameters.items():
            if pname in input_ports:
                continue
            if p.default is not inspect.Parameter.empty:
                params[pname] = p.default
        params.update(_PARAM_OVERRIDES.get(nd.declared_id, {}))
        spec = {
            "id": nd.declared_id,
            "type": _DELEGATES.get(nd.declared_id, nd.manifest.id),
            "params": params,
        }
        spec.update(_NODE_SETTINGS.get(nd.declared_id, {}))
        nodes.append(spec)
        for port, wire in nd.wires.items():
            source, _, output = wire.partition(".")
            edges.append(
                {
                    "id": f"w{seq}",
                    "source": source,
                    "source_output": output or "main",
                    "target": nd.declared_id,
                    "target_input": port,
                }
            )
            seq += 1
    nodes.extend(_EXTRA_NODES)
    edges.extend(_EXTRA_EDGES)
    return {"nodes": nodes, "edges": edges}


def _runtime_registry():
    """Built-ins plus any hand-written custom nodes (ids not in _DELEGATES)."""
    merged = NodeRegistry()
    merged._nodes.update(builtin_registry._nodes)  # noqa: SLF001
    for nd in workflow_registry._nodes.values():  # noqa: SLF001
        if nd.declared_id not in _DELEGATES:
            merged._nodes[nd.manifest.id] = nd  # noqa: SLF001
    return merged


def _seed_trigger(graph, value):
    for spec in graph.get("nodes", []):
        node_type = str(spec.get("type") or "")
        if node_type in _TRIGGER_TYPES or node_type.endswith("_trigger"):
            return {spec["id"]: {"main": value if value is not None else {}}}
    return None


async def _run_subworkflow(call):
    graph = SUBWORKFLOWS.get(call.workflow_id)
    if graph is None:
        raise RuntimeError(
            f"sub-workflow '{call.workflow_id}' is not bundled in this export"
        )
    return InlineSubworkflow(
        graph=graph,
        cache=_seed_trigger(graph, call.parameters),
        targets=None,
        sources=tuple(str(e.get("source")) for e in graph.get("edges", [])),
    )


def main() -> None:
    meta = SubworkflowMeta(call_chain=frozenset({ROOT_ID} if ROOT_ID else ()))
    result = run(
        WorkflowGraph.model_validate(_build_graph()),
        _runtime_registry(),
        subworkflow_runner=_run_subworkflow,
        subworkflow_meta=meta,
    )
    print(f"workflow finished: {result.status}")
    for node_id, node_result in result.nodes.items():
        print(f"  {node_id}: {node_result.status}")
        if node_result.error:
            print(f"    error: {node_result.error}")


if __name__ == "__main__":
    main()
'''


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "workflow"


def _identifier(node_id: str, used: set[str]) -> str:
    base = re.sub(r"\W", "_", node_id) or "node_fn"
    if base[0].isdigit() or keyword.iskeyword(base):
        base = f"n_{base}"
    ident = base
    suffix = 2
    while ident in used:
        ident = f"{base}_{suffix}"
        suffix += 1
    used.add(ident)
    return ident


def _instance_settings(graph_node: GraphNode) -> dict:
    """GraphNode fields that differ from defaults (position, on_error, ...)."""
    dumped = graph_node.model_dump(exclude={"id", "type", "params"})
    return {
        key: value
        for key, value in dumped.items()
        if value != _DEFAULT_NODE_FIELDS.get(key)
    }


def workflow_to_module(
    graph: WorkflowGraph,
    name: str,
    *,
    registry,
    subworkflows: dict[str, dict] | None = None,
    root_id: str | None = None,
) -> str:
    """Render a workflow graph as a code-first Python module.

    ``registry`` is the live node registry (``noodle.sdk.registry`` with
    ``noodle_nodes`` imported) used to look up manifests, input ports, and
    async-ness for each node type.
    """
    delegates: dict[str, str] = {}
    node_settings: dict[str, dict] = {}
    param_overrides: dict[str, dict] = {}
    extra_nodes: list[dict] = []
    function_blocks: list[str] = []
    used_idents: set[str] = set()
    decorated_ids: set[str] = set()

    for graph_node in graph.nodes:
        if graph_node.type not in registry:
            extra_nodes.append(graph_node.model_dump())
            continue
        node_def = registry.get(graph_node.type)
        manifest = node_def.manifest
        decorated_ids.add(graph_node.id)
        delegates[graph_node.id] = graph_node.type

        settings = _instance_settings(graph_node)
        if settings:
            node_settings[graph_node.id] = settings

        # Required manifest params (manifest defaults) overlaid with the
        # instance's configured params.
        params: dict = {}
        for spec in manifest.params:
            if spec.required:
                params[spec.name] = spec.default
        params.update(graph_node.params or {})

        signature_params: dict = {}
        overrides: dict = {}
        for pname, value in params.items():
            if pname.isidentifier() and not keyword.iskeyword(pname):
                signature_params[pname] = value
            else:
                overrides[pname] = value
        if overrides:
            param_overrides[graph_node.id] = overrides

        input_ports = [p.name for p in manifest.inputs]
        output_ports = [p.name for p in manifest.outputs]
        ident = _identifier(graph_node.id, used_idents)

        sig_parts = [f"{port}=None" for port in input_ports]
        sig_parts += [f"{k}={v!r}" for k, v in signature_params.items()]
        forward_parts = [f"{n}={n}" for n in [*input_ports, *signature_params]]

        is_async = node_def.is_async
        def_kw = "async def" if is_async else "def"
        call = "_call_builtin_async" if is_async else "_call_builtin"
        ret = "return await" if is_async else "return"
        label = graph_node.label or manifest.name

        block = (
            "@node(\n"
            f"    name={label!r},\n"
            f"    id={graph_node.id!r},\n"
            '    category="Exported",\n'
            f"    role={manifest.role.value!r},\n"
            f"    inputs={input_ports!r},\n"
            f"    outputs={output_ports!r},\n"
            "    registry=workflow_registry,\n"
            ")\n"
            f"{def_kw} {ident}({', '.join(sig_parts)}):\n"
            f"    \"\"\"{graph_node.type} node from the Noodle workflow.\"\"\"\n"
            f"    {ret} {call}({graph_node.type!r}"
            + (", " + ", ".join(forward_parts) if forward_parts else "")
            + ")\n"
        )
        function_blocks.append(block)

    # Edges: first edge per (target, input port) -> wires; the rest (and any
    # edge touching an _EXTRA_NODES node) -> _EXTRA_EDGES.
    wires_by_target: dict[str, dict[str, str]] = {}
    extra_edges: list[dict] = []
    for edge in graph.edges:
        wire_value = (
            edge.source
            if edge.source_output in ("", "main")
            else f"{edge.source}.{edge.source_output}"
        )
        target_wires = wires_by_target.setdefault(edge.target, {})
        if edge.target in decorated_ids and edge.target_input not in target_wires:
            target_wires[edge.target_input] = wire_value
        else:
            extra_edges.append(edge.model_dump())

    # Inject wires into the generated decorator blocks.
    finished_blocks: list[str] = []
    for block in function_blocks:
        match = re.search(r"^    id=(.+?),$", block, flags=re.MULTILINE)
        node_id = eval(match.group(1))  # noqa: S307 - our own repr round-trip
        wires = wires_by_target.get(node_id, {})
        block = block.replace(
            "    registry=workflow_registry,\n",
            f"    wires={wires!r},\n    registry=workflow_registry,\n",
        )
        finished_blocks.append(block)

    header = _HEADER_TEMPLATE.replace("__NAME__", name).replace(
        "__SLUG__", _slugify(name)
    )
    footer = (
        _FOOTER_TEMPLATE.replace("__DELEGATES__", repr(delegates))
        .replace("__NODE_SETTINGS__", repr(node_settings))
        .replace("__PARAM_OVERRIDES__", repr(param_overrides))
        .replace("__EXTRA_NODES__", repr(extra_nodes))
        .replace("__EXTRA_EDGES__", repr(extra_edges))
        .replace("__SUBWORKFLOWS__", json.dumps(subworkflows or {}, indent=2))
        .replace("__ROOT_ID__", repr(root_id))
    )
    return header + "\n\n" + "\n\n".join(finished_blocks) + footer
```

Then add to `packages/exporter/noodle_exporter/__init__.py` (mirror the existing exports):

```python
from noodle_exporter.module_codegen import workflow_to_module
```

and append `"workflow_to_module"` to its `__all__` if one exists.

Implementation note for the `eval` line: it round-trips our own `repr()` of the node id one line above — if the repo's linter blocks `eval` even with `noqa`, restructure to build blocks as `(node_id, text)` tuples instead of regex-extracting the id (preferred; do this if convenient).

- [ ] **Step 4: Run tests**

Run: `python -m pytest packages/exporter/tests/test_module_codegen.py -v`
Expected: all PASS. Most likely failure: `test_main_runs_trigger_only_graph` if `run()` needs extra kwargs — compare against `_SCRIPT_TEMPLATE` in `codegen.py` (the existing template's `main()` is the reference; keep the generated `main()` identical to it apart from `_build_graph()`/`_runtime_registry()`).

- [ ] **Step 5: Commit**

```bash
git add packages/exporter/noodle_exporter/module_codegen.py packages/exporter/noodle_exporter/__init__.py packages/exporter/tests/
git commit -m "feat(exporter): code-first module export - @node-decorated function per workflow node"
```

---

### Task C2: API endpoint + export menu entry

**Files:**
- Modify: `apps/api/app/routers/export.py`
- Modify: `apps/web/src/EditorPage.tsx` (export dropdown, lines ~1214–1236)
- Test: `apps/api/tests/test_export.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_export.py`:

```python
async def test_export_python_module(client: AsyncClient) -> None:
    workflow_id = await _workflow(client)
    resp = await client.get(f"/workflows/{workflow_id}/export.module.py")
    assert resp.status_code == 200
    assert "@node(" in resp.text
    assert "workflow_registry" in resp.text
    assert "manual_trigger" in resp.text
    assert "attachment" in resp.headers["content-disposition"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/api/tests/test_export.py -v`
Expected: new test FAILS with 404.

- [ ] **Step 3: Add the endpoint**

In `apps/api/app/routers/export.py`, extend the exporter import to include `workflow_to_module`, add `from noodle.models import WorkflowGraph` and `from noodle.sdk import registry as node_registry`, plus `import noodle_nodes  # noqa: F401` near the top, then add after `export_script`:

```python
@router.get("/workflows/{workflow_id}/export.module.py")
async def export_module(
    workflow_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    """Code-first export: one @node-decorated function per workflow node."""
    workflow = await _load(session, workflow_id)
    graph = workflow.draft_graph or workflow.versions[-1].graph or EMPTY_GRAPH
    subs = await _collect_subworkflows(session, graph)
    script = workflow_to_module(
        WorkflowGraph.model_validate(graph),
        workflow.name,
        registry=node_registry,
        subworkflows=subs,
        root_id=workflow.id,
    )
    return Response(
        script,
        media_type="text/x-python",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{slugify(workflow.name)}_module.py"'
            )
        },
    )
```

- [ ] **Step 4: Add the menu entry**

In `apps/web/src/EditorPage.tsx`, inside the export dropdown (after the existing `Python script (.py)` link at line ~1228):

```tsx
                <a
                  href={`/api/workflows/${id}/export.module.py`}
                  onClick={() => setExportOpen(false)}
                >
                  Python module (code-first .py)
                </a>
```

- [ ] **Step 5: Run tests + build**

Run: `python -m pytest apps/api/tests/test_export.py -v` → all PASS
Run: `npm --prefix apps/web run build` → compiles

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/export.py apps/web/src/EditorPage.tsx apps/api/tests/test_export.py
git commit -m "feat(api,web): export.module.py endpoint + editor menu entry for code-first export"
```

---

### Task C3: Realistic round-trip test (two-node pipeline executes through the engine)

**Files:**
- Modify: `packages/exporter/tests/test_module_codegen.py` (append)

- [ ] **Step 1: Append an execution test using a deterministic builtin**

Pick a side-effect-free builtin that transforms data. `edit_fields` (seen in `noodle_nodes/builtin.py`) is the candidate; first verify its manifest with:

```
python -c "import noodle_nodes; from noodle.sdk import registry; m = registry.get('edit_fields').manifest; print([p.name for p in m.params], [p.name for p in m.inputs])"
```

Then write the test (adjust the `fields` param shape to what the manifest/source expects — read the `edit_fields` function in `packages/nodes/noodle_nodes/builtin.py` first):

```python
def test_two_node_pipeline_executes() -> None:
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {"id": "t", "type": "manual_trigger", "params": {}},
                {
                    "id": "shape",
                    "type": "edit_fields",
                    # Adjust to edit_fields' real param shape (read builtin.py).
                    "params": {"fields": {"greeting": "hello"}},
                },
            ],
            "edges": [
                {"id": "e1", "source": "t", "target": "shape", "target_input": "input"}
            ],
        }
    )
    module = _load_module(workflow_to_module(graph, "Pipeline", registry=registry))
    built = module["_build_graph"]()
    parsed = WorkflowGraph.model_validate(built)

    from noodle.engine import run

    result = run(parsed, module["_runtime_registry"]())
    assert result.status == "success"
    assert result.nodes["shape"].status == "success"
```

- [ ] **Step 2: Run**

Run: `python -m pytest packages/exporter/tests/test_module_codegen.py -v`
Expected: all PASS. If `edit_fields` needs different params, fix the test's params (NOT the codegen) to match the real manifest.

- [ ] **Step 3: Run full affected suites once more**

Run: `python -m pytest packages/exporter/tests packages/nodes/tests packages/core/tests -q && python -m pytest apps/api/tests -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/exporter/tests/test_module_codegen.py
git commit -m "test(exporter): code-first module round-trip executes a two-node pipeline"
```

---

### Task C4: Document export modes

**Files:**
- Modify: `docs/mcp.md` → no; Create: `docs/export-modes.md`

- [ ] **Step 1: Write the doc**

```markdown
# Export modes

| Mode | Endpoint | Shape | Best for |
|------|----------|-------|----------|
| Python script | `GET /workflows/{id}/export.py` | graph embedded as JSON, engine runs it | running a frozen copy elsewhere |
| Docker bundle | `GET /workflows/{id}/export/docker` | script + Dockerfile + requirements | shipping as an image |
| Python module (code-first) | `GET /workflows/{id}/export.module.py` | one `@node` function per node, `main()` rebuilds + runs | reading, editing, extending in code |

## Code-first module: what is editable

- **Param defaults** on each function — `main()` reads them back via `inspect`,
  so editing them reconfigures the run.
- **`wires={...}`** — the graph's edges.
- **New `@node` functions** — become real custom nodes executed by the engine.
- Function **bodies** of generated wrappers are conveniences for calling a node
  standalone; the engine executes the original built-in type (`_DELEGATES`).

## Limitations (both .py modes)

- Credential references resolve against the Noodle server and will not decrypt
  in a standalone script — replace them with literals or environment lookups.
- `user:` code-module nodes are carried as raw graph entries; their Python
  lives in the Noodle database, so bundle it manually if needed.
- Expressions (`{{ $json... }}`) work unchanged — the engine evaluates them.
```

- [ ] **Step 2: Commit**

```bash
git add docs/export-modes.md
git commit -m "docs: export modes - script vs docker vs code-first module"
```

---

## Final verification checklist (run before opening the PR)

- [ ] `python -m pytest apps/api/tests -q` — green
- [ ] `python -m pytest packages/exporter/tests packages/nodes/tests packages/core/tests -q` — green
- [ ] `npm --prefix apps/web run build` — green
- [ ] Manual smoke (optional, needs a running stack): `claude mcp add --transport http noodle http://localhost:8000/mcp` then ask Claude Code to `list_workflows`
- [ ] New migration applies cleanly: `alembic upgrade head` from `apps/api` against a dev database

## Out of scope (deliberate)

- stdio MCP client transport (worker-host process spawning — security).
- MCP server features beyond tools (resources, prompts, sampling, SSE streams, sessions).
- OAuth flows for MCP client credentials (bearer/header auth only).
- Long-lived API tokens for MCP clients (use session tokens; a dedicated PAT endpoint can be a follow-up).
- Docker bundle variant of the code-first export.
