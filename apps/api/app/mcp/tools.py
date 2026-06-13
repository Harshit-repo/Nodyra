"""MCP tool registry: static builder/runner tools + per-workflow dynamic tools.

Each tool couples a JSON-Schema input contract with an async handler. The
``permission`` key maps into the existing RBAC table
(``app.security._PERMISSION_MIN_ROLE``); ``None`` means viewer-level access.
Handlers raise :class:`McpToolError` for anything the calling model should
read and recover from — the router renders it as an ``isError`` tool result.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import noodle_nodes  # noqa: F401 - registers built-in nodes
from app.db import SessionLocal
from app.models import NodeRun, Run, RunEvent, User, Workflow, WorkflowVersion
from app.services.audit import log_audit
from app.services.runner import start_run
from app.services.triggers import _await_run_terminal, _last_node_output
from noodle.models import WorkflowGraph
from noodle.sdk import registry as node_registry
from noodle_exporter import slugify

logger = logging.getLogger(__name__)

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
    # populate_existing: the instance may already sit in the session's
    # identity map WITHOUT versions loaded (e.g. via _mcp_enabled_workflows),
    # and a plain get() would skip the query — and the eager-load — entirely,
    # leaving .versions to blow up on async lazy-load.
    workflow = await session.get(
        Workflow,
        workflow_id,
        options=[selectinload(Workflow.versions)],
        populate_existing=True,
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
    # Filter and cap in SQL, and don't eager-load every workflow's full
    # version history just to count nodes — agents call this at the start of
    # nearly every session, and with hundreds of versioned workflows that was
    # tens of MB of graph JSON per call.
    stmt = select(Workflow).order_by(Workflow.updated_at.desc()).limit(limit)
    if search:
        stmt = stmt.where(func.lower(Workflow.name).contains(search, autoescape=True))
    rows = (await session.scalars(stmt)).all()

    # Workflows without a draft fall back to the latest version's graph for
    # node_count — fetch only those graphs, in one query.
    need_version = [wf.id for wf in rows if not wf.draft_graph]
    latest_graph: dict[str, dict] = {}
    if need_version:
        versions = await session.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id.in_(need_version))
            .order_by(WorkflowVersion.workflow_id, WorkflowVersion.version.desc())
        )
        for v in versions.all():
            latest_graph.setdefault(v.workflow_id, v.graph or EMPTY_GRAPH)

    out: list[dict] = []
    for wf in rows:
        graph = wf.draft_graph or latest_graph.get(wf.id, EMPTY_GRAPH)
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
    result: dict[str, Any] = {
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
    if run.status == "error" and not result["nodes"]:
        run_err_evt = await session.scalar(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.event_type == "run_error")
            .limit(1)
        )
        if run_err_evt is not None and isinstance(run_err_evt.payload, dict):
            result["error"] = run_err_evt.payload.get("error")
            result.pop("nodes")  # remove empty list; error field is the signal
    return result


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
            node_errors = [
                {"node_id": nr.node_id, "error": nr.error} for nr in rows
            ]
            if node_errors:
                result["errors"] = node_errors
            else:
                # No node-level errors — check for a run-level error event
                # (e.g. credential resolution or graph dispatch failure that
                # occurred before any nodes ran).
                run_err_evt = await session.scalar(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                        RunEvent.event_type == "run_error",
                    )
                    .limit(1)
                )
                if run_err_evt is not None and isinstance(run_err_evt.payload, dict):
                    result["error"] = run_err_evt.payload.get("error")
                else:
                    result["errors"] = []
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
    # Release the request-scoped connection before the (up to 300 s) wait —
    # _run_outcome opens its own session, and holding this one open would pin
    # a pooled connection per concurrent MCP run_workflow call.
    await session.close()
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


# ---------------------------------------------------------------------------
# Builder tools
# ---------------------------------------------------------------------------


def _validate_graph_payload(graph: Any) -> WorkflowGraph:
    if not isinstance(graph, dict):
        raise McpToolError('graph must be an object: {"nodes": [...], "edges": [...]}')
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
            WorkflowPublishRequest(notes=str(args.get("notes") or "")),
            session,
            user,
        )
    except Exception as exc:
        raise McpToolError(f"Publish failed: {exc}") from exc
    return response.model_dump()


# ---------------------------------------------------------------------------
# Static tool list
# ---------------------------------------------------------------------------

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
]


def get_tool(name: str) -> McpTool | None:
    for tool in STATIC_TOOLS:
        if tool.name == name:
            return tool
    return None


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
    # No version eager-load: descriptors only need the mcp_* columns, and the
    # call path re-loads the chosen workflow (with versions) by id anyway.
    rows = await session.scalars(
        select(Workflow)
        .where(Workflow.mcp_enabled.is_(True))
        .order_by(Workflow.updated_at.desc())
    )
    return list(rows.all())


async def list_workflow_tool_descriptors(session: AsyncSession) -> list[dict]:
    out: list[dict] = []
    static_names = {t.name for t in STATIC_TOOLS}
    seen: set[str] = set()
    for wf in await _mcp_enabled_workflows(session):
        name = workflow_tool_name(wf)
        if name in static_names or name in seen:
            logger.warning(
                "MCP tool name collision: '%s' (workflow %s) shadows an earlier "
                "definition — set a unique mcp_tool_name on this workflow",
                name,
                wf.id,
            )
            continue
        seen.add(name)
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
