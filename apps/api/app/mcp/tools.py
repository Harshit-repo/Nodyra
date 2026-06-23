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
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import noodle_nodes  # noqa: F401 - registers built-in nodes
from app.db import SessionLocal
from app.models import Deployment, NodeRun, Run, RunEvent, User, Workflow, WorkflowVersion
from app.routers.workflows import STRUCTURAL_NODE_TYPES
from app.services.audit import log_audit
from app.services.github_sync import enqueue_github_push
from app.services.github_sync_jobs import notify_sync_workers
from app.services.runner import cancel_run as _runner_cancel_run, start_run
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


async def _list_workflows(session: AsyncSession, user: User | None, args: dict) -> Any:
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


async def _get_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    return {
        "id": workflow.id,
        "name": workflow.name,
        "active": workflow.active,
        "published_version": workflow.published_version,
        "graph": _draft_graph(workflow),
    }


async def _list_node_types(session: AsyncSession, user: User | None, args: dict) -> Any:
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


async def _get_node_type(session: AsyncSession, user: User | None, args: dict) -> Any:
    node_type = str(args.get("node_type") or "")
    for manifest in node_registry.manifests():
        if manifest.id == node_type:
            return manifest.model_dump(mode="json")
    raise McpToolError(f"Unknown node type: {node_type!r}. Use list_node_types to discover ids.")


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


async def _list_runs(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    status_filter = str(args.get("status") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 100))

    stmt = select(Run).order_by(Run.started_at.desc()).limit(limit)
    if workflow_id:
        stmt = stmt.where(Run.workflow_id == workflow_id)
    if status_filter:
        stmt = stmt.where(Run.status == status_filter)

    runs = (await session.scalars(stmt)).all()
    return {
        "runs": [
            {
                "run_id": r.id,
                "workflow_id": r.workflow_id,
                "status": r.status,
                "trigger_type": r.trigger_type,
                "mode": r.mode,
                "started_at": str(r.started_at),
                "finished_at": str(r.finished_at) if r.finished_at else None,
            }
            for r in runs
        ]
    }


async def _get_run_events(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise McpToolError("run_id is required.")
    limit = max(1, min(int(args.get("limit") or 50), 200))
    after_seq = int(args.get("after_sequence") or 0)

    run = await session.get(Run, run_id)
    if run is None:
        raise McpToolError(f"Run not found: {run_id}")

    events = (
        await session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.sequence > after_seq)
            .order_by(RunEvent.sequence)
            .limit(limit)
        )
    ).all()
    return {
        "run_id": run_id,
        "events": [
            {
                "event_type": e.event_type,
                "sequence": e.sequence,
                "ts": str(e.ts),
                "node_id": e.node_id,
                "payload": _truncated(e.payload),
            }
            for e in events
        ],
    }


async def _get_workflow_stats(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise McpToolError(f"Workflow not found: {workflow_id}")

    row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.sum(case((Run.status == "success", 1), else_=0)).label("success_count"),
                func.sum(case((Run.status == "error", 1), else_=0)).label("error_count"),
                func.max(Run.started_at).label("last_run_at"),
            ).where(Run.workflow_id == workflow_id)
        )
    ).one()
    return {
        "workflow_id": workflow_id,
        "total_runs": row.total or 0,
        "success_count": row.success_count or 0,
        "error_count": row.error_count or 0,
        "last_run_at": str(row.last_run_at) if row.last_run_at else None,
    }


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
                    select(NodeRun).where(NodeRun.run_id == run_id, NodeRun.status == "error")
                )
            ).all()
            node_errors = [{"node_id": nr.node_id, "error": nr.error} for nr in rows]
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


async def _run_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
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


async def _cancel_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise McpToolError("run_id is required.")
    run = await session.get(Run, run_id)
    if run is None:
        raise McpToolError(f"Run not found: {run_id}")
    status = await _runner_cancel_run(run_id)
    return {"run_id": run_id, "status": status or run.status}


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
            if n.type
            and n.type not in known
            and n.type not in STRUCTURAL_NODE_TYPES
            and not n.type.startswith("user:")
        }
    )
    if unknown:
        raise McpToolError(
            "Unknown node types: "
            + ", ".join(unknown)
            + ". Use list_node_types / get_node_type to discover valid ids."
        )
    return parsed


async def _create_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
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
        session,
        "create",
        "workflow",
        detail=f"mcp: {name}",
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    return {"workflow_id": workflow.id, "name": name}


async def _set_workflow_graph(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    parsed = _validate_graph_payload(args.get("graph"))
    workflow.draft_graph = parsed.model_dump()
    await log_audit(
        session,
        "mcp_set_graph",
        "workflow",
        workflow.id,
        workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    return {
        "workflow_id": workflow.id,
        "node_count": len(parsed.nodes),
        "edge_count": len(parsed.edges),
        "hint": "Draft saved. Use run_workflow (use_draft=true) to test, then publish_workflow.",
    }


async def _validate_graph(session: AsyncSession, user: User | None, args: dict) -> Any:
    parsed = _validate_graph_payload(args.get("graph"))
    return {"valid": True, "node_count": len(parsed.nodes), "edge_count": len(parsed.edges)}


async def _publish_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
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


async def _patch_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise McpToolError("node_id is required.")
    params = args.get("params")
    if not isinstance(params, dict):
        raise McpToolError("params must be a JSON object.")

    graph = _draft_graph(workflow)
    nodes = list(graph.get("nodes", []))
    for i, node in enumerate(nodes):
        if node.get("id") == node_id:
            merged = {**node.get("params", {}), **params}
            nodes[i] = {**node, "params": merged}
            workflow.draft_graph = {**graph, "nodes": nodes}
            await log_audit(
                session,
                "mcp_patch_node",
                "workflow",
                workflow.id,
                workflow.name,
                actor_id=user.id if user else None,
                actor_email=user.email if user else None,
            )
            await session.commit()
            return {"workflow_id": workflow.id, "node_id": node_id, "params": merged}
    raise McpToolError(f"Node not found in draft graph: {node_id}")


async def _add_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node = args.get("node")
    if not isinstance(node, dict):
        raise McpToolError("node must be a JSON object with id, type, params.")
    node_id = str(node.get("id") or "").strip()
    node_type = str(node.get("type") or "").strip()
    if not node_id or not node_type:
        raise McpToolError("node.id and node.type are required.")

    graph = _draft_graph(workflow)
    nodes = list(graph.get("nodes", []))
    if any(n.get("id") == node_id for n in nodes):
        raise McpToolError(f"Node id already exists in draft graph: {node_id!r}")

    known = {m.id for m in node_registry.manifests()}
    if (
        node_type not in known
        and node_type not in STRUCTURAL_NODE_TYPES
        and not node_type.startswith("user:")
    ):
        raise McpToolError(
            f"Unknown node type: {node_type!r}. Use list_node_types to discover valid ids."
        )

    nodes.append(node)
    workflow.draft_graph = {**graph, "nodes": nodes}
    await log_audit(
        session, "mcp_add_node", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "node_id": node_id, "node_count": len(nodes)}


async def _remove_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise McpToolError("node_id is required.")

    graph = _draft_graph(workflow)
    original_count = len(graph.get("nodes", []))
    nodes = [n for n in graph.get("nodes", []) if n.get("id") != node_id]
    if len(nodes) == original_count:
        raise McpToolError(f"Node not found in draft graph: {node_id!r}")

    edges = [
        e for e in graph.get("edges", [])
        if e.get("source") != node_id and e.get("target") != node_id
    ]
    workflow.draft_graph = {**graph, "nodes": nodes, "edges": edges}
    await log_audit(
        session, "mcp_remove_node", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "node_id": node_id, "removed": True}


async def _add_edge(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    edge = args.get("edge")
    if not isinstance(edge, dict):
        raise McpToolError("edge must be a JSON object with source and target.")
    source = str(edge.get("source") or "").strip()
    target = str(edge.get("target") or "").strip()
    if not source or not target:
        raise McpToolError("edge.source and edge.target are required.")

    graph = _draft_graph(workflow)
    node_ids = {n.get("id") for n in graph.get("nodes", [])}
    if source not in node_ids:
        raise McpToolError(f"Source node not found: {source!r}")
    if target not in node_ids:
        raise McpToolError(f"Target node not found: {target!r}")

    edges = list(graph.get("edges", []))
    edges.append(edge)
    workflow.draft_graph = {**graph, "edges": edges}
    await log_audit(
        session, "mcp_add_edge", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "edge_count": len(edges)}


async def _remove_edge(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    source = str(args.get("source") or "").strip()
    target = str(args.get("target") or "").strip()
    if not source or not target:
        raise McpToolError("source and target are required.")
    source_output = args.get("source_output")
    target_input = args.get("target_input")

    graph = _draft_graph(workflow)
    edges = list(graph.get("edges", []))

    def _matches(e: dict) -> bool:
        if e.get("source") != source or e.get("target") != target:
            return False
        if source_output is not None and e.get("source_output") != source_output:
            return False
        if target_input is not None and e.get("target_input") != target_input:
            return False
        return True

    remaining = [e for e in edges if not _matches(e)]
    removed_count = len(edges) - len(remaining)
    if removed_count == 0:
        raise McpToolError(f"No matching edge found: {source!r} → {target!r}")

    workflow.draft_graph = {**graph, "edges": remaining}
    await log_audit(
        session, "mcp_remove_edge", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "removed_count": removed_count}


# ---------------------------------------------------------------------------
# Lifecycle tools
# ---------------------------------------------------------------------------


async def _toggle_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    active = args.get("active")
    if not isinstance(active, bool):
        raise McpToolError("active must be a boolean (true or false).")
    workflow.active = active
    await log_audit(
        session, "toggle_active", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "active": workflow.active}


async def _list_workflow_versions(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise McpToolError(f"Workflow not found: {workflow_id}")

    versions = (
        await session.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version.desc())
        )
    ).all()
    return {
        "workflow_id": workflow_id,
        "current_published_version": workflow.published_version,
        "versions": [
            {"id": v.id, "version": v.version, "notes": v.notes, "created_at": str(v.created_at)}
            for v in versions
        ],
    }


async def _rollback_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    version_num = args.get("version")
    if not isinstance(version_num, int):
        raise McpToolError("version must be an integer.")

    target = await session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version == version_num,
        )
    )
    if target is None:
        raise McpToolError(f"Version {version_num} not found for workflow {workflow.id!r}.")

    workflow.draft_graph = dict(target.graph or EMPTY_GRAPH)
    await log_audit(
        session, "rollback", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {
        "workflow_id": workflow.id,
        "draft_restored_from_version": version_num,
        "hint": "Draft replaced. Use run_workflow (use_draft=true) to test, then publish_workflow.",
    }


async def _delete_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    await log_audit(
        session, "delete", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.delete(workflow)
    await session.commit()
    return {"deleted": True, "workflow_id": workflow.id}


async def _duplicate_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    from app.routers.workflows import _global_env_id

    source = await _load_workflow(session, str(args.get("workflow_id") or ""))
    new_name = str(args.get("name") or "").strip() or f"{source.name} (copy)"
    graph = _draft_graph(source)

    new_wf = Workflow(
        name=new_name,
        environment_id=source.environment_id or await _global_env_id(session),
        draft_graph=dict(graph),
        published_version=1,
    )
    new_wf.versions.append(WorkflowVersion(version=1, graph=dict(graph)))
    session.add(new_wf)
    await log_audit(
        session, "duplicate", "workflow", new_wf.id, new_name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {
        "workflow_id": new_wf.id,
        "name": new_name,
        "source_workflow_id": source.id,
    }


# ---------------------------------------------------------------------------
# Schedule tools
# ---------------------------------------------------------------------------


async def _list_schedules(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    active_filter = args.get("active")
    limit = max(1, min(int(args.get("limit") or 50), 200))

    stmt = select(Deployment).order_by(Deployment.updated_at.desc()).limit(limit)
    if workflow_id:
        stmt = stmt.where(Deployment.workflow_id == workflow_id)
    if isinstance(active_filter, bool):
        stmt = stmt.where(Deployment.active == active_filter)

    deployments = (await session.scalars(stmt)).all()
    return {
        "schedules": [
            {
                "schedule_id": d.id,
                "workflow_id": d.workflow_id,
                "name": d.name,
                "schedule_cron": d.schedule_cron,
                "schedule_interval": d.schedule_interval,
                "schedule_every": d.schedule_every,
                "schedule_tz": d.schedule_tz,
                "active": d.active,
                "last_fired": str(d.last_fired) if d.last_fired else None,
            }
            for d in deployments
        ]
    }


async def _create_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    name = str(args.get("name") or "").strip()
    if not name:
        raise McpToolError("name is required.")

    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise McpToolError(f"Workflow not found: {workflow_id}")

    default_parameters = args.get("default_parameters") or {}
    if not isinstance(default_parameters, dict):
        raise McpToolError("default_parameters must be a JSON object.")

    deployment = Deployment(
        workflow_id=workflow_id,
        org_id=workflow.org_id,
        name=name,
        schedule_cron=str(args.get("schedule_cron") or ""),
        schedule_interval=str(args.get("schedule_interval") or "hours"),
        schedule_every=max(1, int(args.get("schedule_every") or 1)),
        schedule_tz=str(args.get("schedule_tz") or ""),
        default_parameters=default_parameters,
        active=True,
    )
    session.add(deployment)
    await log_audit(
        session, "create_schedule", "deployment", deployment.id, name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"schedule_id": deployment.id, "workflow_id": workflow_id, "name": name}


async def _delete_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        raise McpToolError("schedule_id is required.")
    deployment = await session.get(Deployment, schedule_id)
    if deployment is None:
        raise McpToolError(f"Schedule not found: {schedule_id}")
    await log_audit(
        session, "delete_schedule", "deployment", schedule_id, deployment.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.delete(deployment)
    await session.commit()
    return {"deleted": True, "schedule_id": schedule_id}


async def _toggle_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        raise McpToolError("schedule_id is required.")
    active = args.get("active")
    if not isinstance(active, bool):
        raise McpToolError("active must be a boolean.")
    deployment = await session.get(Deployment, schedule_id)
    if deployment is None:
        raise McpToolError(f"Schedule not found: {schedule_id}")
    deployment.active = active
    await session.commit()
    return {"schedule_id": schedule_id, "active": active}


# ---------------------------------------------------------------------------
# New handlers: workflow config, graph inspection, code nodes, ops
# ---------------------------------------------------------------------------


async def _rename_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    name = str(args.get("name") or "").strip()
    if not name:
        raise McpToolError("name is required.")
    old_name = workflow.name
    workflow.name = name
    await log_audit(
        session, "mcp_rename_workflow", "workflow", workflow.id, f"{old_name} → {name}",
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    return {"workflow_id": workflow.id, "name": name}


async def _get_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise McpToolError("node_id is required.")
    graph = _draft_graph(workflow)
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            return node
    raise McpToolError(f"Node not found in draft graph: {node_id!r}")


async def _rename_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    label = str(args.get("label") or "").strip()
    if not node_id:
        raise McpToolError("node_id is required.")
    if not label:
        raise McpToolError("label is required.")
    graph = _draft_graph(workflow)
    nodes = list(graph.get("nodes", []))
    for i, node in enumerate(nodes):
        if node.get("id") == node_id:
            nodes[i] = {**node, "label": label}
            workflow.draft_graph = {**graph, "nodes": nodes}
            await log_audit(
                session, "mcp_rename_node", "workflow", workflow.id, workflow.name,
                actor_id=user.id if user else None,
                actor_email=user.email if user else None,
            )
            await session.commit()
            return {"workflow_id": workflow.id, "node_id": node_id, "label": label}
    raise McpToolError(f"Node not found in draft graph: {node_id!r}")


async def _move_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise McpToolError("node_id is required.")
    x = args.get("x")
    y = args.get("y")
    if x is None or y is None:
        raise McpToolError("x and y are required.")
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        raise McpToolError("x and y must be numbers.")
    graph = _draft_graph(workflow)
    nodes = list(graph.get("nodes", []))
    for i, node in enumerate(nodes):
        if node.get("id") == node_id:
            pos = {**node.get("position", {}), "x": x, "y": y}
            nodes[i] = {**node, "position": pos}
            workflow.draft_graph = {**graph, "nodes": nodes}
            await session.commit()
            return {"workflow_id": workflow.id, "node_id": node_id, "position": pos}
    raise McpToolError(f"Node not found in draft graph: {node_id!r}")


async def _create_code_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    code = str(args.get("code") or "output = input")
    label = str(args.get("label") or "").strip() or None
    x = float(args.get("x") or 0)
    y = float(args.get("y") or 0)
    if not node_id:
        raise McpToolError("node_id is required.")
    graph = _draft_graph(workflow)
    nodes = list(graph.get("nodes", []))
    if any(n.get("id") == node_id for n in nodes):
        raise McpToolError(f"Node id already exists: {node_id!r}")
    entry: dict = {"id": node_id, "type": "code", "params": {"code": code}, "position": {"x": x, "y": y}}
    if label:
        entry["label"] = label
    nodes.append(entry)
    workflow.draft_graph = {**graph, "nodes": nodes}
    await log_audit(
        session, "mcp_create_code_node", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "node_id": node_id, "node_count": len(nodes)}


async def _update_code(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    code = args.get("code")
    if not node_id:
        raise McpToolError("node_id is required.")
    if not isinstance(code, str):
        raise McpToolError("code must be a string.")
    graph = _draft_graph(workflow)
    nodes = list(graph.get("nodes", []))
    for i, node in enumerate(nodes):
        if node.get("id") == node_id:
            if node.get("type") != "code":
                raise McpToolError(f"Node {node_id!r} is type {node.get('type')!r}, not 'code'.")
            nodes[i] = {**node, "params": {**node.get("params", {}), "code": code}}
            workflow.draft_graph = {**graph, "nodes": nodes}
            await log_audit(
                session, "mcp_update_code", "workflow", workflow.id, workflow.name,
                actor_id=user.id if user else None,
                actor_email=user.email if user else None,
            )
            await session.commit()
            return {"workflow_id": workflow.id, "node_id": node_id}
    raise McpToolError(f"Node not found in draft graph: {node_id!r}")


async def _retry_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    from app.routers.runs import retry_from_failure as _retry_route

    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise McpToolError("run_id is required.")
    try:
        result = await _retry_route(run_id, session)
    except Exception as exc:
        raise McpToolError(str(exc)) from exc
    return {"new_run_id": result.run_id, "retried_from": run_id}


async def _list_environments(session: AsyncSession, user: User | None, args: dict) -> Any:
    from app.models import Environment, RunnerPool

    rows = (
        await session.scalars(
            select(Environment).order_by(Environment.is_global.desc(), Environment.name)
        )
    ).all()
    return {
        "environments": [
            {
                "id": e.id,
                "name": e.name,
                "is_global": e.is_global,
                "python_version": e.python_version,
                "packages": e.packages,
                "status": e.status,
                "backend": getattr(e, "backend", "venv"),
            }
            for e in rows
        ]
    }


async def _list_credentials(session: AsyncSession, user: User | None, args: dict) -> Any:
    from app.models import Credential

    rows = (
        await session.scalars(select(Credential).order_by(Credential.name).limit(500))
    ).all()
    return {
        "credentials": [
            {
                "id": c.id,
                "name": c.name,
                "type": c.type,
                "scope": c.scope,
                "description": getattr(c, "description", "") or "",
                "workflow_id": c.workflow_id,
                "environment_id": c.environment_id,
            }
            for c in rows
        ]
    }


async def _set_error_handler(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    error_workflow_id = str(args.get("error_workflow_id") or "").strip() or None
    if error_workflow_id:
        if error_workflow_id == workflow.id:
            raise McpToolError("A workflow cannot use itself as its error handler.")
        err_wf = await session.get(Workflow, error_workflow_id)
        if err_wf is None:
            raise McpToolError(f"Error handler workflow not found: {error_workflow_id}")
    workflow.error_workflow_id = error_workflow_id
    await log_audit(
        session, "mcp_set_error_handler", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "error_workflow_id": error_workflow_id}


async def _enable_mcp_tool(session: AsyncSession, user: User | None, args: dict) -> Any:
    import re

    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    tool_name = str(args.get("tool_name") or "").strip()
    description = str(args.get("description") or "").strip() or None
    if tool_name and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool_name):
        raise McpToolError("tool_name must match [A-Za-z0-9_-]{1,64}.")
    workflow.mcp_enabled = True
    if tool_name:
        workflow.mcp_tool_name = tool_name
    if description:
        workflow.mcp_description = description
    await log_audit(
        session, "mcp_enable_mcp_tool", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    notify_sync_workers()
    return {
        "workflow_id": workflow.id,
        "mcp_enabled": True,
        "tool_name": workflow.mcp_tool_name or workflow.name,
        "description": workflow.mcp_description,
    }


async def _disable_mcp_tool(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    workflow.mcp_enabled = False
    await log_audit(
        session, "mcp_disable_mcp_tool", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    notify_sync_workers()
    return {"workflow_id": workflow.id, "mcp_enabled": False}


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
        permission="workflow:run",
        handler=_get_run,
    ),
    McpTool(
        name="list_runs",
        description=(
            "List recent runs, optionally filtered by workflow_id and/or status. "
            "status values: running, queued, waiting, success, error, cancelled."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Filter to one workflow."},
                "status": {"type": "string", "description": "Filter by run status."},
                "limit": {"type": "integer", "description": "Max results (1-100, default 20)."},
            },
        },
        permission="workflow:run",
        handler=_list_runs,
    ),
    McpTool(
        name="get_run_events",
        description=(
            "Full event log for a run (run_started, node_started, node_finished, "
            "run_error, etc.). Use after_sequence to page through large logs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "limit": {"type": "integer", "description": "Max events (1-200, default 50)."},
                "after_sequence": {"type": "integer", "description": "Skip events at or before this sequence."},
            },
            "required": ["run_id"],
        },
        permission="workflow:run",
        handler=_get_run_events,
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
        name="cancel_run",
        description="Cancel a running or queued workflow run. Returns the resulting status.",
        input_schema={
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
        permission="workflow:run",
        handler=_cancel_run,
    ),
    McpTool(
        name="get_workflow_stats",
        description="Aggregate stats for a workflow: total runs, success/error counts, last run time.",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        permission=None,
        handler=_get_workflow_stats,
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
    McpTool(
        name="patch_node",
        description=(
            "Merge params into a single node in the draft graph without replacing the whole graph. "
            "Existing params not mentioned in the patch are preserved."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
                "params": {"type": "object", "description": "Partial params to merge into the node."},
            },
            "required": ["workflow_id", "node_id", "params"],
        },
        permission="workflow:write",
        handler=_patch_node,
    ),
    McpTool(
        name="add_node",
        description=(
            "Add a single node to the draft graph. node = {id, type, params, position?}. "
            "node.type must be a valid id from list_node_types."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "type": {"type": "string"},
                        "params": {"type": "object"},
                        "position": {"type": "object"},
                    },
                    "required": ["id", "type"],
                },
            },
            "required": ["workflow_id", "node"],
        },
        permission="workflow:write",
        handler=_add_node,
    ),
    McpTool(
        name="remove_node",
        description="Remove a node and all its connected edges from the draft graph.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["workflow_id", "node_id"],
        },
        permission="workflow:write",
        handler=_remove_node,
    ),
    McpTool(
        name="add_edge",
        description=(
            "Add an edge to the draft graph. "
            "edge = {source, target, source_output?, target_input?}. "
            "Both source and target node ids must already exist in the graph."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "edge": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "source_output": {"type": "string"},
                        "target_input": {"type": "string"},
                    },
                    "required": ["source", "target"],
                },
            },
            "required": ["workflow_id", "edge"],
        },
        permission="workflow:write",
        handler=_add_edge,
    ),
    McpTool(
        name="remove_edge",
        description=(
            "Remove an edge from the draft graph by source and target node ids. "
            "Optionally narrow with source_output / target_input when multiple edges connect the same pair."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "source": {"type": "string"},
                "target": {"type": "string"},
                "source_output": {"type": "string"},
                "target_input": {"type": "string"},
            },
            "required": ["workflow_id", "source", "target"],
        },
        permission="workflow:write",
        handler=_remove_edge,
    ),
    McpTool(
        name="toggle_workflow",
        description="Activate or deactivate a workflow (controls whether scheduled triggers fire).",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "active": {"type": "boolean"},
            },
            "required": ["workflow_id", "active"],
        },
        permission="workflow:write",
        handler=_toggle_workflow,
    ),
    McpTool(
        name="list_workflow_versions",
        description="List all published versions of a workflow, newest first.",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        permission=None,
        handler=_list_workflow_versions,
    ),
    McpTool(
        name="rollback_workflow",
        description=(
            "Restore a published version's graph to the draft. Does not publish — "
            "call publish_workflow afterwards to make it permanent."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "version": {"type": "integer", "description": "Version number from list_workflow_versions."},
            },
            "required": ["workflow_id", "version"],
        },
        permission="workflow:write",
        handler=_rollback_workflow,
    ),
    McpTool(
        name="delete_workflow",
        description="Permanently delete a workflow and all its runs, versions, and events.",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_delete_workflow,
    ),
    McpTool(
        name="duplicate_workflow",
        description=(
            "Clone a workflow's current draft graph into a new workflow. "
            "Optionally specify a name; defaults to '{original} (copy)'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "name": {"type": "string", "description": "Name for the new workflow."},
            },
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_duplicate_workflow,
    ),
    McpTool(
        name="list_schedules",
        description="List cron schedules (deployments). Filter by workflow_id or active state.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "active": {"type": "boolean"},
                "limit": {"type": "integer", "description": "Max results (1-200, default 50)."},
            },
        },
        permission=None,
        handler=_list_schedules,
    ),
    McpTool(
        name="create_schedule",
        description=(
            "Create a cron schedule for a workflow. "
            "Supply schedule_cron (e.g. '0 * * * *') OR schedule_interval+schedule_every. "
            "The schedule starts active immediately."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "name": {"type": "string"},
                "schedule_cron": {"type": "string", "description": "Full cron expression (overrides interval fields)."},
                "schedule_interval": {"type": "string", "description": "minutes / hours / days / weeks."},
                "schedule_every": {"type": "integer", "description": "Multiplier for schedule_interval."},
                "schedule_tz": {"type": "string", "description": "IANA timezone, e.g. America/New_York."},
                "default_parameters": {"type": "object", "description": "Default trigger payload."},
            },
            "required": ["workflow_id", "name"],
        },
        permission="workflow:write",
        handler=_create_schedule,
    ),
    McpTool(
        name="delete_schedule",
        description="Permanently delete a cron schedule.",
        input_schema={
            "type": "object",
            "properties": {"schedule_id": {"type": "string"}},
            "required": ["schedule_id"],
        },
        permission="workflow:write",
        handler=_delete_schedule,
    ),
    McpTool(
        name="toggle_schedule",
        description="Activate or deactivate a cron schedule without deleting it.",
        input_schema={
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string"},
                "active": {"type": "boolean"},
            },
            "required": ["schedule_id", "active"],
        },
        permission="workflow:write",
        handler=_toggle_schedule,
    ),
    # --- new tools ---
    McpTool(
        name="rename_workflow",
        description="Rename a workflow.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "name": {"type": "string", "description": "New workflow name."},
            },
            "required": ["workflow_id", "name"],
        },
        permission="workflow:write",
        handler=_rename_workflow,
    ),
    McpTool(
        name="get_node",
        description="Return a single node object (id, type, params, label, position) from the draft graph.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["workflow_id", "node_id"],
        },
        permission=None,
        handler=_get_node,
    ),
    McpTool(
        name="rename_node",
        description="Set the display label of a node in the draft graph.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
                "label": {"type": "string", "description": "Human-readable display name for the node."},
            },
            "required": ["workflow_id", "node_id", "label"],
        },
        permission="workflow:write",
        handler=_rename_node,
    ),
    McpTool(
        name="move_node",
        description="Update a node's canvas position (x, y) in the draft graph.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            "required": ["workflow_id", "node_id", "x", "y"],
        },
        permission="workflow:write",
        handler=_move_node,
    ),
    McpTool(
        name="create_code_node",
        description=(
            "Add a Code node to the draft graph. Use this when no built-in node type covers the task — "
            "the LLM writes Python; `input` is the upstream value, assign result to `output`. "
            "For multiple output ports use `output_<name>` variables."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string", "description": "Unique id for the new node."},
                "code": {"type": "string", "description": "Python body. Assign result to `output`."},
                "label": {"type": "string", "description": "Display name shown on the canvas."},
                "x": {"type": "number", "description": "Canvas x position (default 0)."},
                "y": {"type": "number", "description": "Canvas y position (default 0)."},
            },
            "required": ["workflow_id", "node_id", "code"],
        },
        permission="workflow:write",
        handler=_create_code_node,
    ),
    McpTool(
        name="update_code",
        description="Replace the Python code on an existing Code node.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
                "code": {"type": "string", "description": "New Python body. Assign result to `output`."},
            },
            "required": ["workflow_id", "node_id", "code"],
        },
        permission="workflow:write",
        handler=_update_code,
    ),
    McpTool(
        name="retry_run",
        description=(
            "Re-run only the failed nodes and their descendants, reusing all successful node outputs. "
            "Returns a new run_id."
        ),
        input_schema={
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
        permission="workflow:run",
        handler=_retry_run,
    ),
    McpTool(
        name="list_environments",
        description="List all Python environments (id, name, python_version, packages, status, backend).",
        input_schema={"type": "object", "properties": {}},
        permission=None,
        handler=_list_environments,
    ),
    McpTool(
        name="list_credentials",
        description=(
            "List all credential sets by name and type (no secret values returned). "
            "Use to discover what credentials are available to reference in node params."
        ),
        input_schema={"type": "object", "properties": {}},
        permission=None,
        handler=_list_credentials,
    ),
    McpTool(
        name="set_error_handler",
        description=(
            "Set or clear the error-handler workflow for a workflow. "
            "When a run fails, Noodle will trigger error_workflow_id with the error details. "
            "Pass error_workflow_id=null to remove the handler."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "error_workflow_id": {
                    "type": ["string", "null"],
                    "description": "Id of the error-handler workflow, or null to clear.",
                },
            },
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_set_error_handler,
    ),
    McpTool(
        name="enable_mcp_tool",
        description=(
            "Expose a workflow as an MCP tool so other agents can call it by name. "
            "Sets mcp_enabled=true and optionally sets the tool name and description."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "tool_name": {
                    "type": "string",
                    "description": "Tool name (alphanumeric/underscore/dash, max 64 chars). Defaults to workflow name.",
                },
                "description": {"type": "string", "description": "What this tool does (shown to calling models)."},
            },
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_enable_mcp_tool,
    ),
    McpTool(
        name="disable_mcp_tool",
        description="Remove a workflow from the MCP tool surface (sets mcp_enabled=false).",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_disable_mcp_tool,
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
        select(Workflow).where(Workflow.mcp_enabled.is_(True)).order_by(Workflow.updated_at.desc())
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
                "inputSchema": schema
                if isinstance(schema, dict) and schema
                else _PERMISSIVE_SCHEMA,
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
