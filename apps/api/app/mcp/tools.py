"""MCP tool registry: static builder/runner tools + per-workflow dynamic tools.

Each tool couples a JSON-Schema input contract with an async handler. The
``permission`` key maps into the existing RBAC table
(``app.security._PERMISSION_MIN_ROLE``); ``None`` means viewer-level access.
Handlers raise :class:`McpToolError` for anything the calling model should
read and recover from — the router renders it as an ``isError`` tool result.
"""

import difflib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import ValidationError
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import nodyra_nodes  # noqa: F401 - registers built-in nodes
from app.db import SessionLocal
from app.mcp.guidance import (
    compact_node_contract,
    node_llm_guidance,
    workflow_authoring_guide,
)
from app.models import (
    Deployment,
    Environment,
    EnvironmentBuildJob,
    NodeRun,
    Run,
    RunEvent,
    User,
    Workflow,
    WorkflowRevision,
    WorkflowVersion,
)
from app.routers.workflows import STRUCTURAL_NODE_TYPES
from app.services.audit import log_audit
from app.services.data_ref import resolve_ref
from app.services.environment_builds import (
    enqueue_environment_build,
    notify_environment_build_workers,
)
from app.services.github_sync import enqueue_github_push
from app.services.github_sync_jobs import notify_sync_workers
from app.services.graph_utils import first_trigger_node
from app.services.runner import cancel_run as _runner_cancel_run
from app.services.runner import start_run
from app.services.sandbox_policy import (
    VALID_EXECUTION_MODES,
    validate_sandbox_resources,
)
from app.services.triggers import _await_run_terminal, _last_node_output
from app.services.workflow_events import (
    WORKFLOW_CREATED,
    WORKFLOW_DELETED,
    WORKFLOW_UPDATED,
    bump_graph_revision,
    edge_patch_snapshot,
    node_patch_snapshot,
    publish_workflow_event,
    publish_workflow_graph_changed,
    record_workflow_revision,
)
from nodyra.engine.scheduler import _topo_order
from nodyra.engine.types import GraphError
from nodyra.engine.validation import _validate_connection_kinds
from nodyra.models import WorkflowGraph
from nodyra.sdk import registry as node_registry
from nodyra_exporter import slugify

logger = logging.getLogger(__name__)

EMPTY_GRAPH: dict = {"nodes": [], "edges": []}
MAX_WAIT_SECONDS = 300.0
DEFAULT_WAIT_SECONDS = 60.0
OUTPUT_TRUNCATE_BYTES = 8000
EXPECTED_GRAPH_REVISION_SCHEMA = {
    "type": "integer",
    "minimum": 0,
    "description": (
        "Optional optimistic concurrency guard. Pass the graph_revision returned "
        "by get_workflow/list_workflows so stale agent edits fail instead of "
        "overwriting canvas changes."
    ),
}


class McpToolError(Exception):
    """Tool-level failure whose message goes back to the calling model."""


DESTRUCTIVE_TOOL_HINTS = {
    "apply_workflow_patch",
    "create_schedule",
    "delete_schedule",
    "delete_workflow",
    "publish_workflow",
    "remove_node",
    "remove_edge",
    "rollback_workflow",
    "set_workflow_graph",
    "toggle_schedule",
    "update_schedule",
    "set_environment_packages",
    "remove_environment_package",
}

OPEN_WORLD_TOOL_HINTS = {
    "run_workflow",
    "retry_run",
    "create_environment",
    "add_environment_package",
    "set_environment_packages",
    "remove_environment_package",
    "rebuild_environment",
}

APPROVAL_REQUIRED_TOOL_NAMES = {
    "add_environment_package",
    "apply_workflow_patch",
    "create_environment",
    "create_schedule",
    "delete_schedule",
    "delete_workflow",
    "publish_workflow",
    "rebuild_environment",
    "remove_edge",
    "remove_environment_package",
    "remove_node",
    "rollback_workflow",
    "set_environment_packages",
    "set_workflow_graph",
    "toggle_schedule",
    "update_schedule",
}

MCP_HUMAN_APPROVAL_PROPERTY = {
    "type": "boolean",
    "description": (
        "Required for this production-impacting operation. Set to true only "
        "after an explicit human approval in the client conversation."
    ),
}


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict
    permission: str | None
    handler: Callable[[AsyncSession, User | None, dict], Awaitable[Any]]

    def descriptor(self) -> dict:
        read_only = self.permission is None or self.name.startswith(("get_", "list_"))
        destructive = (
            self.name in DESTRUCTIVE_TOOL_HINTS
            or self.name.startswith(("delete_", "remove_", "cancel_"))
        )
        input_schema = self.input_schema
        if self.name in APPROVAL_REQUIRED_TOOL_NAMES:
            input_schema = {
                **self.input_schema,
                "properties": {
                    **self.input_schema.get("properties", {}),
                    "approved_by_user": MCP_HUMAN_APPROVAL_PROPERTY,
                },
            }
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": input_schema,
            "outputSchema": {"type": "object", "additionalProperties": True},
            "annotations": {
                "readOnlyHint": read_only,
                "destructiveHint": destructive,
                "idempotentHint": read_only or self.name.startswith(("set_", "toggle_", "update_", "rename_", "move_")),
                "openWorldHint": self.name in OPEN_WORLD_TOOL_HINTS,
                "requiresHumanApprovalHint": self.name in APPROVAL_REQUIRED_TOOL_NAMES,
            },
            "execution": {"taskSupport": "forbidden"},
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


def _publish_mcp_graph_change(
    workflow: Workflow,
    operation: str,
    user: User | None,
    **extra: Any,
) -> None:
    graph = _draft_graph(workflow)
    publish_workflow_graph_changed(
        workflow,
        origin="mcp",
        operation=operation,
        actor=user,
        node_count=len(graph.get("nodes", [])),
        edge_count=len(graph.get("edges", [])),
        **extra,
    )


def _record_mcp_graph_revision(
    session: AsyncSession,
    workflow: Workflow,
    operation: str,
    user: User | None,
    patch: dict[str, Any] | None = None,
    *,
    summary: str | None = None,
) -> None:
    record_workflow_revision(
        session,
        workflow,
        origin="mcp",
        operation=operation,
        actor=user,
        patch=patch,
        summary=summary,
    )


def _check_expected_graph_revision(workflow: Workflow, args: dict) -> None:
    expected = args.get("expected_graph_revision")
    if expected is None:
        return
    try:
        expected_int = int(expected)
    except (TypeError, ValueError) as exc:
        raise McpToolError("expected_graph_revision must be an integer.") from exc
    if expected_int != int(workflow.graph_revision or 0):
        raise McpToolError(
            "Workflow draft changed before this edit completed "
            f"(expected graph_revision {expected_int}, current "
            f"{workflow.graph_revision}). Reload the workflow and retry."
        )


def _require_explicit_mcp_approval(args: dict, tool_name: str, action: str) -> None:
    if args.get("approved_by_user") is True:
        return
    raise McpToolError(
        f"{tool_name} requires explicit human approval before it can {action}. "
        "Ask the user to confirm, then retry with approved_by_user=true."
    )


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
                "graph_revision": wf.graph_revision,
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
        "graph_revision": workflow.graph_revision,
        "graph": _draft_graph(workflow),
    }


async def _get_workflow_authoring_guide(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    goal = str(args.get("goal") or "").strip()
    detail = str(args.get("detail") or "standard").strip() or "standard"
    return workflow_authoring_guide(goal=goal, detail=detail)


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
            payload = manifest.model_dump(mode="json")
            payload["llm_guidance"] = node_llm_guidance(manifest)
            payload["graph_node_shape"] = compact_node_contract(
                manifest, include_examples=False
            )["graph_node_shape"]
            return payload
    raise McpToolError(f"Unknown node type: {node_type!r}. Use list_node_types to discover ids.")


async def _get_node_contracts(session: AsyncSession, user: User | None, args: dict) -> Any:
    requested = {
        str(item).strip()
        for item in (args.get("node_types") or [])
        if str(item).strip()
    }
    category = str(args.get("category") or "").strip().lower()
    search = str(args.get("search") or args.get("query") or "").strip().lower()
    include_examples = bool(args.get("include_examples", True))
    limit = max(1, min(int(args.get("limit") or 25), 100))
    contracts: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    total = 0
    for manifest in node_registry.manifests():
        if manifest.hidden or manifest.deprecated:
            continue
        if requested and manifest.id not in requested:
            continue
        if category and manifest.category.lower() != category:
            continue
        haystack = " ".join(
            [manifest.id, manifest.name, manifest.category, manifest.description]
        ).lower()
        if search and search not in haystack:
            continue
        matched_ids.add(manifest.id)
        total += 1
        if len(contracts) < limit:
            contracts.append(
                compact_node_contract(manifest, include_examples=include_examples)
            )
    missing = sorted(requested - matched_ids) if requested else []
    return {
        "contracts": contracts,
        "total": total,
        "missing_node_types": missing,
        "next_steps": [
            "Use suggest_node_config for starter node JSON.",
            "Use validate_graph or validate_workflow_graph before running.",
            "Use get_workflow_authoring_guide for graph-level production rules.",
        ],
    }


async def _search_node_catalog(session: AsyncSession, user: User | None, args: dict) -> Any:
    query = str(args.get("query") or args.get("search") or "").strip().lower()
    category = str(args.get("category") or "").strip().lower()
    package = str(args.get("package") or "").strip().lower()
    include_ports = bool(args.get("include_ports", True))
    include_params = bool(args.get("include_params", False))
    include_deprecated = bool(args.get("include_deprecated", False))
    limit = max(1, min(int(args.get("limit") or 30), 100))

    from nodyra.packages import canonical_package_name

    package_key = canonical_package_name(package) if package else ""
    results: list[tuple[int, dict[str, Any]]] = []
    for manifest in node_registry.manifests():
        if manifest.hidden:
            continue
        if manifest.deprecated and not include_deprecated:
            continue
        if category and manifest.category.lower() != category:
            continue
        requirements = list(manifest.requirements or [])
        if package_key and package_key not in {canonical_package_name(req) for req in requirements}:
            continue
        haystack = " ".join(
            [
                manifest.id,
                manifest.name,
                manifest.category,
                manifest.description,
                " ".join(requirements),
            ]
        ).lower()
        if query and query not in haystack:
            continue
        score = 0
        if query:
            if query == manifest.id.lower():
                score += 100
            if query in manifest.name.lower():
                score += 40
            if query in manifest.id.lower():
                score += 30
            if query in manifest.description.lower():
                score += 10
        item: dict[str, Any] = {
            "id": manifest.id,
            "name": manifest.name,
            "category": manifest.category,
            "description": manifest.description,
            "requirements": requirements,
            "deprecated": bool(manifest.deprecated),
            "llm_guidance": node_llm_guidance(manifest),
        }
        if include_ports:
            item["inputs"] = [port.model_dump(mode="json") for port in manifest.inputs]
            item["outputs"] = [port.model_dump(mode="json") for port in manifest.outputs]
        if include_params:
            item["params"] = [
                {
                    "name": param.name,
                    "type": param.type,
                    "required": param.required,
                    "default": param.default,
                    "description": param.description,
                    "choices": param.choices,
                }
                for param in manifest.params
            ]
        results.append((score, item))
    results.sort(key=lambda row: (-row[0], row[1]["category"], row[1]["name"]))
    return {"nodes": [item for _, item in results[:limit]], "total": len(results)}


async def _get_node_schema(session: AsyncSession, user: User | None, args: dict) -> Any:
    return await _get_node_type(session, user, {"node_type": args.get("node_type")})


def _placeholder_for_param(param: Any) -> Any:
    if param.default is not None:
        return param.default
    if param.choices:
        return param.choices[0]
    kind = str(param.type or "").lower()
    if kind in {"int", "integer", "number", "float"}:
        return 0
    if kind in {"bool", "boolean"}:
        return False
    if kind in {"dict", "object", "json"}:
        return {}
    if kind in {"list", "array"}:
        return []
    return ""


async def _suggest_node_config(session: AsyncSession, user: User | None, args: dict) -> Any:
    node_type = str(args.get("node_type") or "").strip()
    node_id = str(args.get("node_id") or node_type or "").strip()
    if not node_type:
        raise McpToolError("node_type is required.")
    for manifest in node_registry.manifests():
        if manifest.id != node_type:
            continue
        params: dict[str, Any] = {}
        required_missing: list[str] = []
        include_optional_defaults = bool(args.get("include_optional_defaults", False))
        for param in manifest.params:
            if param.required:
                params[param.name] = _placeholder_for_param(param)
                if param.default is None and not param.choices:
                    required_missing.append(param.name)
            elif include_optional_defaults and param.default is not None:
                params[param.name] = param.default
        node = {
            "id": node_id,
            "type": node_type,
            "params": params,
            "position": {
                "x": float(args.get("x") or 0),
                "y": float(args.get("y") or 0),
            },
        }
        return {
            "node": node,
            "required_missing": required_missing,
            "inputs": [port.model_dump(mode="json") for port in manifest.inputs],
            "outputs": [port.model_dump(mode="json") for port in manifest.outputs],
            "requirements": list(manifest.requirements or []),
            "llm_guidance": node_llm_guidance(manifest),
            "hint": "Replace placeholder values before applying the node to a workflow.",
        }
    raise McpToolError(f"Unknown node type: {node_type!r}. Use search_node_catalog first.")


async def _get_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "")
    if not run_id:
        raise McpToolError("run_id is required.")
    run = await session.scalar(
        select(Run).where(Run.id == run_id).options(selectinload(Run.node_runs))
    )
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
                # Resolve offloaded-output markers before transport (OS-1).
                "output": _truncated(resolve_ref(nr.output)),
            }
            for nr in run.node_runs
        ],
    }
    if run.status == "timed_out":
        result["error"] = run.error or "workflow run timed out"
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

    run = await session.scalar(select(Run).where(Run.id == run_id))
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
    workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
    if workflow is None:
        raise McpToolError(f"Workflow not found: {workflow_id}")

    row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.sum(case((Run.status == "success", 1), else_=0)).label("success_count"),
                func.sum(case((Run.status.in_(("error", "timed_out")), 1), else_=0)).label(
                    "error_count"
                ),
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


def assert_workflow_runnable_over_mcp(workflow, *, require_opt_in: bool | None = None) -> None:
    """Refuse to run a workflow the operator has not exposed to MCP.

    run_workflow accepts any workflow id, so without this the operator's
    opt-in (mcp_enabled, set by enable_mcp_tool) only governed
    how a workflow was advertised, not whether an agent could invoke it. An
    agent that knew an id could run anything in the org.

    Off unless MCP_RUN_REQUIRES_OPT_IN is set, so existing integrations keep
    working across an upgrade.
    """
    if require_opt_in is None:
        from app.config import settings

        require_opt_in = bool(settings.mcp_run_requires_opt_in)
    if not require_opt_in or getattr(workflow, "mcp_enabled", False):
        return
    label = getattr(workflow, "name", None) or getattr(workflow, "id", "?")
    raise McpToolError(
        f"This deployment only lets MCP run workflows that have been exposed as "
        f"tools, and {label!r} has not been. Ask the workspace owner to run "
        f"enable_mcp_tool for it, or to enable it in the workflow's MCP "
        f"settings. Retrying will not help."
    )


async def run_workflow_by_id(
    session: AsyncSession,
    workflow_id: str,
    *,
    parameters: dict | None,
    wait_seconds: float,
    use_draft: bool,
    sandbox: bool = False,
) -> dict:
    """Shared by the static run_workflow tool and dynamic per-workflow tools."""
    workflow = await _load_workflow(session, workflow_id)
    assert_workflow_runnable_over_mcp(workflow)
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
            execution_mode=("sandboxed" if sandbox else None),
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
    sandbox = bool(args.get("sandbox", False))
    return await run_workflow_by_id(
        session,
        str(args.get("workflow_id") or ""),
        parameters=parameters,
        wait_seconds=wait_seconds,
        use_draft=use_draft,
        sandbox=sandbox,
    )


async def _cancel_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise McpToolError("run_id is required.")
    run = await session.scalar(select(Run).where(Run.id == run_id))
    if run is None:
        raise McpToolError(f"Run not found: {run_id}")
    status = await _runner_cancel_run(run_id)
    return {"run_id": run_id, "status": status or run.status}


# ---------------------------------------------------------------------------
# Builder tools
# ---------------------------------------------------------------------------


def _validate_graph_payload(graph: Any, *, require_trigger: bool = True) -> WorkflowGraph:
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
    node_ids = [node.id for node in parsed.nodes]
    if len(node_ids) != len(set(node_ids)):
        raise McpToolError("Node ids must be unique.")
    known_ids = set(node_ids)
    manifests = {manifest.id: manifest for manifest in node_registry.manifests()}
    for edge in parsed.edges:
        if edge.source not in known_ids or edge.target not in known_ids:
            raise McpToolError(
                f"Edge references a missing node: {edge.source!r} → {edge.target!r}."
            )
        source_node = next(node for node in parsed.nodes if node.id == edge.source)
        target_node = next(node for node in parsed.nodes if node.id == edge.target)
        source_manifest = manifests.get(source_node.type)
        target_manifest = manifests.get(target_node.type)
        if source_manifest and source_manifest.outputs:
            valid = {port.name for port in source_manifest.outputs}
            if source_node.outputs_override:
                valid.update(str(port) for port in source_node.outputs_override if port)
            if source_node.tool_mode:
                valid.add("tool")
            if edge.source_output not in valid:
                raise McpToolError(
                    f"Unknown output port {edge.source}.{edge.source_output}; expected one of {sorted(valid)}."
                )
        if target_manifest and target_manifest.inputs:
            valid = {port.name for port in target_manifest.inputs}
            if edge.target_input not in valid:
                raise McpToolError(
                    f"Unknown input port {edge.target}.{edge.target_input}; expected one of {sorted(valid)}."
                )
    for node in parsed.nodes:
        manifest = manifests.get(node.type)
        if manifest is None:
            continue
        missing = [
            spec.name
            for spec in manifest.params
            if spec.required and spec.name not in node.params and spec.default is None
        ]
        if missing:
            raise McpToolError(
                f"Node {node.id!r} is missing required params: {', '.join(missing)}."
            )
    try:
        _topo_order(parsed)
        _validate_connection_kinds(parsed, node_registry)
    except (GraphError, ValueError) as exc:
        raise McpToolError(f"Invalid graph: {exc}") from exc
    if require_trigger and parsed.nodes and first_trigger_node(parsed.model_dump()) is None:
        raise McpToolError("Workflow graph has no trigger node.")
    return parsed


NODE_PATCH_FIELDS = {
    "label",
    "position",
    "disabled",
    "outputs_override",
    "on_error",
    "retry_on_fail",
    "retries",
    "retry_wait_seconds",
    "retry_backoff",
    "always_output_data",
    "timeout_seconds",
    "hooks",
    "tool_mode",
    "tool_name",
    "tool_description",
}


def _graph_copy(graph: dict | None) -> dict:
    source = graph or EMPTY_GRAPH
    return json.loads(json.dumps(source))


def _edge_key(edge: dict) -> tuple[str, str, str, str]:
    return (
        str(edge.get("source") or ""),
        str(edge.get("source_output") or "main"),
        str(edge.get("target") or ""),
        str(edge.get("target_input") or "input"),
    )


def _edge_matches(edge: dict, selector: dict) -> bool:
    edge_id = str(selector.get("edge_id") or selector.get("id") or "").strip()
    if edge_id:
        return str(edge.get("id") or "") == edge_id
    source = str(selector.get("source") or "").strip()
    target = str(selector.get("target") or "").strip()
    if not source or not target:
        raise McpToolError("remove_edge requires edge_id or source and target.")
    if str(edge.get("source") or "") != source or str(edge.get("target") or "") != target:
        return False
    if "source_output" in selector and str(edge.get("source_output") or "main") != str(selector.get("source_output") or "main"):
        return False
    if "target_input" in selector and str(edge.get("target_input") or "input") != str(selector.get("target_input") or "input"):
        return False
    return True


def _operation_name(operation: dict) -> str:
    return str(operation.get("op") or operation.get("action") or "").strip()


def _apply_graph_operations(graph: dict, operations: Any) -> tuple[dict, list[dict[str, Any]]]:
    if not isinstance(operations, list) or not operations:
        raise McpToolError("operations must be a non-empty array.")
    if len(operations) > 100:
        raise McpToolError("operations is limited to 100 entries per patch.")

    draft = _graph_copy(graph)
    nodes = list(draft.get("nodes") or [])
    edges = list(draft.get("edges") or [])
    draft["nodes"] = nodes
    draft["edges"] = edges
    changes: list[dict[str, Any]] = []

    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise McpToolError(f"operations[{index}] must be an object.")
        op = _operation_name(operation)
        if not op:
            raise McpToolError(f"operations[{index}].op is required.")

        if op == "add_node":
            node = operation.get("node")
            if not isinstance(node, dict):
                raise McpToolError("add_node requires a node object.")
            node_id = str(node.get("id") or "").strip()
            node_type = str(node.get("type") or "").strip()
            if not node_id or not node_type:
                raise McpToolError("add_node requires node.id and node.type.")
            if any(existing.get("id") == node_id for existing in nodes):
                raise McpToolError(f"Node id already exists in draft graph: {node_id!r}")
            nodes.append(node)
            changes.append({"op": "add_node", "node": node_patch_snapshot(node)})
            continue

        if op in {"update_node", "patch_node"}:
            node_id = str(operation.get("node_id") or "").strip()
            if not node_id:
                raise McpToolError(f"{op} requires node_id.")
            for node_index, node in enumerate(nodes):
                if node.get("id") != node_id:
                    continue
                updated = dict(node)
                changed_fields: list[str] = []
                if "params" in operation:
                    params = operation.get("params")
                    if not isinstance(params, dict):
                        raise McpToolError(f"{op}.params must be an object.")
                    if operation.get("replace_params"):
                        updated["params"] = dict(params)
                    else:
                        updated["params"] = {**dict(updated.get("params") or {}), **params}
                    changed_fields.append("params")
                for field in NODE_PATCH_FIELDS:
                    if field in operation:
                        updated[field] = operation[field]
                        changed_fields.append(field)
                if not changed_fields:
                    raise McpToolError(f"{op} must include params or node fields to update.")
                nodes[node_index] = updated
                changes.append(
                    {
                        "op": "update_node",
                        "node_id": node_id,
                        "fields": sorted(set(changed_fields)),
                    }
                )
                break
            else:
                raise McpToolError(f"Node not found in draft graph: {node_id!r}")
            continue

        if op == "move_node":
            node_id = str(operation.get("node_id") or "").strip()
            if not node_id:
                raise McpToolError("move_node requires node_id.")
            position = operation.get("position")
            if isinstance(position, dict):
                x = position.get("x")
                y = position.get("y")
            else:
                x = operation.get("x")
                y = operation.get("y")
            try:
                next_position = {"x": float(x), "y": float(y)}
            except (TypeError, ValueError) as exc:
                raise McpToolError("move_node requires numeric x and y.") from exc
            for node_index, node in enumerate(nodes):
                if node.get("id") == node_id:
                    nodes[node_index] = {**node, "position": next_position}
                    changes.append({"op": "move_node", "node_id": node_id, "position": next_position})
                    break
            else:
                raise McpToolError(f"Node not found in draft graph: {node_id!r}")
            continue

        if op == "rename_node":
            node_id = str(operation.get("node_id") or "").strip()
            label = str(operation.get("label") or "").strip()
            if not node_id or not label:
                raise McpToolError("rename_node requires node_id and label.")
            for node_index, node in enumerate(nodes):
                if node.get("id") == node_id:
                    nodes[node_index] = {**node, "label": label}
                    changes.append({"op": "rename_node", "node_id": node_id, "label": label})
                    break
            else:
                raise McpToolError(f"Node not found in draft graph: {node_id!r}")
            continue

        if op == "remove_node":
            node_id = str(operation.get("node_id") or "").strip()
            if not node_id:
                raise McpToolError("remove_node requires node_id.")
            original_node_count = len(nodes)
            original_edge_count = len(edges)
            nodes[:] = [node for node in nodes if node.get("id") != node_id]
            if len(nodes) == original_node_count:
                raise McpToolError(f"Node not found in draft graph: {node_id!r}")
            edges[:] = [
                edge
                for edge in edges
                if edge.get("source") != node_id and edge.get("target") != node_id
            ]
            changes.append(
                {
                    "op": "remove_node",
                    "node_id": node_id,
                    "removed_edge_count": original_edge_count - len(edges),
                }
            )
            continue

        if op == "add_edge":
            edge = operation.get("edge")
            if not isinstance(edge, dict):
                raise McpToolError("add_edge requires an edge object.")
            if not str(edge.get("source") or "").strip() or not str(edge.get("target") or "").strip():
                raise McpToolError("add_edge requires edge.source and edge.target.")
            key = _edge_key(edge)
            if any(_edge_key(existing) == key for existing in edges):
                raise McpToolError(
                    f"Edge already exists: {key[0]}.{key[1]} -> {key[2]}.{key[3]}"
                )
            edges.append(edge)
            changes.append({"op": "add_edge", "edge": edge_patch_snapshot(edge)})
            continue

        if op == "remove_edge":
            original_edge_count = len(edges)
            edges[:] = [edge for edge in edges if not _edge_matches(edge, operation)]
            removed = original_edge_count - len(edges)
            if removed == 0:
                raise McpToolError("No matching edge found to remove.")
            changes.append({"op": "remove_edge", "removed_edge_count": removed})
            continue

        raise McpToolError(
            f"Unsupported graph patch op {op!r}. Expected one of: "
            "add_node, update_node, move_node, rename_node, remove_node, add_edge, remove_edge."
        )

    return draft, changes


def _graph_requirements(parsed: WorkflowGraph) -> list[str]:
    manifests = {manifest.id: manifest for manifest in node_registry.manifests()}
    seen: dict[str, str] = {}
    from nodyra.packages import canonical_package_name

    for node in parsed.nodes:
        manifest = manifests.get(node.type)
        if manifest is None:
            continue
        for requirement in manifest.requirements or []:
            seen[canonical_package_name(requirement)] = requirement
    return [seen[key] for key in sorted(seen)]


async def _graph_validation_summary(
    session: AsyncSession,
    graph: dict,
    *,
    workflow: Workflow | None = None,
    require_trigger: bool = True,
) -> dict[str, Any]:
    try:
        parsed = _validate_graph_payload(graph, require_trigger=require_trigger)
    except McpToolError as exc:
        return {"valid": False, "error": str(exc)}

    required_packages = _graph_requirements(parsed)
    missing_packages: list[str] = []
    workflow_requirements_missing: list[str] = []
    environment: dict[str, Any] | None = None
    if workflow:
        env: Environment | None = None
        if workflow.environment_id:
            env = await session.get(Environment, workflow.environment_id)
        else:
            env = await session.scalar(
                select(Environment).where(Environment.is_global.is_(True)).limit(1)
            )
        if env is not None:
            from nodyra.packages import canonical_package_name

            installed = {canonical_package_name(package) for package in env.packages or []}
            missing_packages = [
                requirement
                for requirement in required_packages
                if canonical_package_name(requirement) not in installed
            ]
            from app.services.package_preflight import missing_workflow_requirements

            workflow_requirements_missing = missing_workflow_requirements(
                workflow_requirements=list(workflow.requirements or []),
                installed=list(env.packages or []),
            )
            environment = {
                "id": env.id,
                "name": env.name,
                "status": env.status,
                "packages": list(env.packages or []),
            }
    return {
        "valid": True,
        "node_count": len(parsed.nodes),
        "edge_count": len(parsed.edges),
        "required_packages": required_packages,
        "missing_packages": missing_packages,
        "workflow_requirements_missing": workflow_requirements_missing,
        "environment": environment,
    }


def _validate_mcp_parameters_schema(schema: Any) -> dict | None:
    if schema is None:
        return None
    if not isinstance(schema, dict):
        raise McpToolError("parameters_schema must be a JSON Schema object.")
    if schema.get("type") not in (None, "object"):
        raise McpToolError("parameters_schema must describe a JSON object.")
    candidate = {"type": "object", **schema}
    try:
        validator_for(candidate).check_schema(candidate)
    except SchemaError as exc:
        raise McpToolError(f"Invalid parameters_schema: {exc.message}") from exc
    return candidate


def validate_tool_arguments(schema: dict, arguments: dict) -> None:
    validator = validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(arguments), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.path)
        location = f" at {path}" if path else ""
        raise McpToolError(f"Invalid tool arguments{location}: {error.message}")


async def _create_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    name = str(args.get("name") or "").strip()
    if not name:
        raise McpToolError("name is required.")
    execution_mode = str(args.get("execution_mode") or "inherit")
    if execution_mode not in VALID_EXECUTION_MODES:
        raise McpToolError(f"execution_mode must be one of {VALID_EXECUTION_MODES}.")
    sandbox_resources = None
    if "sandbox_resources" in args and args["sandbox_resources"] is not None:
        try:
            sandbox_resources = validate_sandbox_resources(args["sandbox_resources"])
        except ValueError as exc:
            raise McpToolError(str(exc)) from exc
    from app.routers.workflows import _global_env_id

    workflow = Workflow(
        name=name,
        environment_id=await _global_env_id(session),
        draft_graph=dict(EMPTY_GRAPH),
        published_version=1,
        execution_mode=execution_mode,
        sandbox_resources=sandbox_resources,
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
    publish_workflow_event(
        workflow,
        WORKFLOW_CREATED,
        origin="mcp",
        operation="create",
        actor=user,
    )
    return {"workflow_id": workflow.id, "name": name, "graph_revision": workflow.graph_revision}


async def _set_workflow_graph(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "set_workflow_graph", "replace a workflow draft graph")
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
    parsed = _validate_graph_payload(args.get("graph"))
    workflow.draft_graph = parsed.model_dump()
    bump_graph_revision(workflow)
    patch = {
        "type": "graph_replaced",
        "node_count": len(parsed.nodes),
        "edge_count": len(parsed.edges),
    }
    _record_mcp_graph_revision(session, workflow, "set_graph", user, patch)
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
    _publish_mcp_graph_change(workflow, "set_graph", user, patch=patch)
    return {
        "workflow_id": workflow.id,
        "graph_revision": workflow.graph_revision,
        "node_count": len(parsed.nodes),
        "edge_count": len(parsed.edges),
        "hint": "Draft saved. Use run_workflow (use_draft=true) to test, then publish_workflow.",
    }


async def _validate_graph(session: AsyncSession, user: User | None, args: dict) -> Any:
    parsed = _validate_graph_payload(
        args.get("graph"),
        require_trigger=bool(args.get("require_trigger", True)),
    )
    return {"valid": True, "node_count": len(parsed.nodes), "edge_count": len(parsed.edges)}


async def _validate_workflow_graph(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow: Workflow | None = None
    workflow_id = str(args.get("workflow_id") or "").strip()
    if workflow_id:
        workflow = await _load_workflow(session, workflow_id)
    graph = args.get("graph")
    if graph is None:
        if workflow is None:
            raise McpToolError("workflow_id or graph is required.")
        graph = _draft_graph(workflow)
    require_trigger = bool(args.get("require_trigger", True))
    summary = await _graph_validation_summary(
        session,
        graph,
        workflow=workflow,
        require_trigger=require_trigger,
    )
    if workflow is not None:
        summary["workflow_id"] = workflow.id
        summary["graph_revision"] = workflow.graph_revision
    return summary


async def _preview_workflow_patch(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
    next_graph, changes = _apply_graph_operations(
        _draft_graph(workflow),
        args.get("operations"),
    )
    require_trigger = bool(args.get("require_trigger", True))
    summary = await _graph_validation_summary(
        session,
        next_graph,
        workflow=workflow,
        require_trigger=require_trigger,
    )
    summary.update(
        {
            "workflow_id": workflow.id,
            "current_graph_revision": workflow.graph_revision,
            "changes": changes,
            "change_count": len(changes),
        }
    )
    if args.get("include_graph"):
        summary["graph"] = next_graph
    return summary


async def _apply_workflow_patch(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "apply_workflow_patch", "modify a workflow draft graph")
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
    next_graph, changes = _apply_graph_operations(
        _draft_graph(workflow),
        args.get("operations"),
    )
    require_trigger = bool(args.get("require_trigger", True))
    summary = await _graph_validation_summary(
        session,
        next_graph,
        workflow=workflow,
        require_trigger=require_trigger,
    )
    if not summary.get("valid"):
        raise McpToolError(f"Patch produced invalid graph: {summary.get('error')}")
    parsed = _validate_graph_payload(next_graph, require_trigger=require_trigger)
    workflow.draft_graph = parsed.model_dump()
    bump_graph_revision(workflow)
    patch = {"type": "graph_patch", "changes": changes, "change_count": len(changes)}
    _record_mcp_graph_revision(
        session,
        workflow,
        "apply_workflow_patch",
        user,
        patch,
        summary=f"Applied {len(changes)} graph patch operation(s).",
    )
    await log_audit(
        session,
        "mcp_apply_workflow_patch",
        "workflow",
        workflow.id,
        f"{workflow.name}: {len(changes)} operation(s)",
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    _publish_mcp_graph_change(workflow, "apply_workflow_patch", user, patch=patch)
    result = {
        "workflow_id": workflow.id,
        "graph_revision": workflow.graph_revision,
        "node_count": len(parsed.nodes),
        "edge_count": len(parsed.edges),
        "changes": changes,
        "missing_packages": summary.get("missing_packages", []),
        "workflow_requirements_missing": summary.get(
            "workflow_requirements_missing", []
        ),
        "hint": "Draft saved atomically. Use run_workflow (use_draft=true) to test.",
    }
    if args.get("include_graph"):
        result["graph"] = parsed.model_dump()
    return result


async def _publish_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "publish_workflow", "publish a workflow version")
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
    _check_expected_graph_revision(workflow, args)
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
            bump_graph_revision(workflow)
            patch = {
                "type": "node_updated",
                "node_id": node_id,
                "param_keys": sorted(str(key) for key in params),
            }
            _record_mcp_graph_revision(session, workflow, "patch_node", user, patch)
            await log_audit(
                session,
                "mcp_patch_node",
                "workflow",
                workflow.id,
                workflow.name,
                actor_id=user.id if user else None,
                actor_email=user.email if user else None,
            )
            await enqueue_github_push(session, workflow, "mcp")
            await session.commit()
            notify_sync_workers()
            _publish_mcp_graph_change(
                workflow,
                "patch_node",
                user,
                node_id=node_id,
                patch=patch,
            )
            return {
                "workflow_id": workflow.id,
                "graph_revision": workflow.graph_revision,
                "node_id": node_id,
                "params": merged,
            }
    raise McpToolError(f"Node not found in draft graph: {node_id}")


async def _add_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
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
    bump_graph_revision(workflow)
    patch = {"type": "node_added", "node": node_patch_snapshot(node)}
    _record_mcp_graph_revision(session, workflow, "add_node", user, patch)
    await log_audit(
        session, "mcp_add_node", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    _publish_mcp_graph_change(
        workflow,
        "add_node",
        user,
        node_id=node_id,
        patch=patch,
    )
    return {
        "workflow_id": workflow.id,
        "graph_revision": workflow.graph_revision,
        "node_id": node_id,
        "node_count": len(nodes),
    }


async def _remove_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "remove_node", "remove a node from the workflow draft")
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise McpToolError("node_id is required.")

    graph = _draft_graph(workflow)
    original_count = len(graph.get("nodes", []))
    original_edge_count = len(graph.get("edges", []))
    nodes = [n for n in graph.get("nodes", []) if n.get("id") != node_id]
    if len(nodes) == original_count:
        raise McpToolError(f"Node not found in draft graph: {node_id!r}")

    edges = [
        e for e in graph.get("edges", [])
        if e.get("source") != node_id and e.get("target") != node_id
    ]
    workflow.draft_graph = {**graph, "nodes": nodes, "edges": edges}
    bump_graph_revision(workflow)
    patch = {
        "type": "node_removed",
        "node_id": node_id,
        "removed_edge_count": original_edge_count - len(edges),
    }
    _record_mcp_graph_revision(session, workflow, "remove_node", user, patch)
    await log_audit(
        session, "mcp_remove_node", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    _publish_mcp_graph_change(
        workflow,
        "remove_node",
        user,
        node_id=node_id,
        patch=patch,
    )
    return {
        "workflow_id": workflow.id,
        "graph_revision": workflow.graph_revision,
        "node_id": node_id,
        "removed": True,
    }


async def _add_edge(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
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
    bump_graph_revision(workflow)
    patch = {"type": "edge_added", "edge": edge_patch_snapshot(edge)}
    _record_mcp_graph_revision(session, workflow, "add_edge", user, patch)
    await log_audit(
        session, "mcp_add_edge", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    _publish_mcp_graph_change(
        workflow,
        "add_edge",
        user,
        source=source,
        target=target,
        patch=patch,
    )
    return {
        "workflow_id": workflow.id,
        "graph_revision": workflow.graph_revision,
        "edge_count": len(edges),
    }


async def _remove_edge(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "remove_edge", "remove an edge from the workflow draft")
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
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
    bump_graph_revision(workflow)
    patch = {
        key: value
        for key, value in {
            "type": "edge_removed",
            "source": source,
            "target": target,
            "source_output": source_output,
            "target_input": target_input,
            "removed_count": removed_count,
        }.items()
        if value is not None
    }
    _record_mcp_graph_revision(session, workflow, "remove_edge", user, patch)
    await log_audit(
        session, "mcp_remove_edge", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    _publish_mcp_graph_change(
        workflow,
        "remove_edge",
        user,
        source=source,
        target=target,
        patch=patch,
    )
    return {
        "workflow_id": workflow.id,
        "graph_revision": workflow.graph_revision,
        "removed_count": removed_count,
    }


# ---------------------------------------------------------------------------
# Lifecycle tools
# ---------------------------------------------------------------------------


async def _toggle_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "toggle_workflow", "change whether a workflow runs live")
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
    from app.services.provider_triggers import sync_workflow_provider_triggers

    await sync_workflow_provider_triggers(
        session,
        workflow,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    publish_workflow_event(
        workflow,
        WORKFLOW_UPDATED,
        origin="mcp",
        operation="toggle_active",
        actor=user,
    )
    return {"workflow_id": workflow.id, "active": workflow.active}


async def _list_workflow_versions(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
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


async def _list_workflow_revisions(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    workflow = await _load_workflow(session, workflow_id)
    limit = max(1, min(int(args.get("limit") or 50), 200))
    revisions = (
        await session.scalars(
            select(WorkflowRevision)
            .where(WorkflowRevision.workflow_id == workflow_id)
            .order_by(WorkflowRevision.graph_revision.desc())
            .limit(limit)
        )
    ).all()
    return {
        "workflow_id": workflow_id,
        "current_graph_revision": workflow.graph_revision,
        "revisions": [
            {
                "id": revision.id,
                "graph_revision": revision.graph_revision,
                "origin": revision.origin,
                "operation": revision.operation,
                "summary": revision.summary,
                "patch": revision.patch,
                "actor_email": revision.actor_email,
                "created_at": str(revision.created_at),
            }
            for revision in revisions
        ],
    }


async def _rollback_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "rollback_workflow", "restore an older graph into the draft")
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
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
    bump_graph_revision(workflow)
    patch = {
        "type": "rollback",
        "draft_restored_from_version": version_num,
    }
    _record_mcp_graph_revision(session, workflow, "rollback", user, patch)
    await log_audit(
        session, "rollback", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    _publish_mcp_graph_change(
        workflow,
        "rollback",
        user,
        draft_restored_from_version=version_num,
        patch=patch,
    )
    return {
        "workflow_id": workflow.id,
        "graph_revision": workflow.graph_revision,
        "draft_restored_from_version": version_num,
        "hint": "Draft replaced. Use run_workflow (use_draft=true) to test, then publish_workflow.",
    }


async def _delete_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "delete_workflow", "permanently delete a workflow")
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    await log_audit(
        session, "delete", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.delete(workflow)
    await session.commit()
    publish_workflow_event(
        workflow,
        WORKFLOW_DELETED,
        origin="mcp",
        operation="delete",
        actor=user,
    )
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
    await enqueue_github_push(session, new_wf, "mcp")
    await session.commit()
    notify_sync_workers()
    publish_workflow_event(
        new_wf,
        WORKFLOW_CREATED,
        origin="mcp",
        operation="duplicate",
        actor=user,
        source_workflow_id=source.id,
    )
    return {
        "workflow_id": new_wf.id,
        "graph_revision": new_wf.graph_revision,
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
    _require_explicit_mcp_approval(args, "create_schedule", "create an active schedule")
    from app.routers.deployments import create_deployment
    from app.schemas import DeploymentCreate

    body = DeploymentCreate(
        workflow_id=str(args.get("workflow_id") or ""),
        name=str(args.get("name") or ""),
        schedule_cron=str(args.get("schedule_cron") or ""),
        schedule_interval=str(args.get("schedule_interval") or "hours"),
        schedule_every=max(1, int(args.get("schedule_every") or 1)),
        schedule_tz=str(args.get("schedule_tz") or ""),
        default_parameters=args.get("default_parameters") or {},
        active=True,
        workflow_version_id=args.get("workflow_version_id"),
        approve_unsafe_nodes=bool(args.get("approve_unsafe_nodes", False)),
    )
    result = await create_deployment(body, session, user)
    payload = result.model_dump(mode="json")
    payload["schedule_id"] = payload.pop("id")
    return payload


async def _delete_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "delete_schedule", "permanently delete a schedule")
    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        raise McpToolError("schedule_id is required.")
    deployment = await session.scalar(select(Deployment).where(Deployment.id == schedule_id))
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
    _require_explicit_mcp_approval(args, "toggle_schedule", "change whether a schedule runs")
    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        raise McpToolError("schedule_id is required.")
    active = args.get("active")
    if not isinstance(active, bool):
        raise McpToolError("active must be a boolean.")
    from app.routers.deployments import update_deployment
    from app.schemas import DeploymentUpdate

    result = await update_deployment(
        schedule_id,
        DeploymentUpdate(
            active=active,
            approve_unsafe_nodes=bool(args.get("approve_unsafe_nodes", False)),
        ),
        session,
        user,
    )
    return {"schedule_id": schedule_id, "active": result.active}


async def _update_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "update_schedule", "change schedule behavior")
    from app.routers.deployments import update_deployment
    from app.schemas import DeploymentUpdate

    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        raise McpToolError("schedule_id is required.")
    allowed = {
        "name", "schedule_cron", "schedule_interval", "schedule_every",
        "schedule_tz", "default_parameters", "active", "workflow_version_id",
        "approve_unsafe_nodes",
    }
    values = {key: value for key, value in args.items() if key in allowed}
    result = await update_deployment(
        schedule_id, DeploymentUpdate(**values), session, user
    )
    payload = result.model_dump(mode="json")
    payload["schedule_id"] = payload.pop("id")
    return payload


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
    publish_workflow_event(
        workflow,
        WORKFLOW_UPDATED,
        origin="mcp",
        operation="rename_workflow",
        actor=user,
    )
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
    _check_expected_graph_revision(workflow, args)
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
            bump_graph_revision(workflow)
            patch = {
                "type": "node_updated",
                "node_id": node_id,
                "label": label,
            }
            _record_mcp_graph_revision(session, workflow, "rename_node", user, patch)
            await log_audit(
                session, "mcp_rename_node", "workflow", workflow.id, workflow.name,
                actor_id=user.id if user else None,
                actor_email=user.email if user else None,
            )
            await enqueue_github_push(session, workflow, "mcp")
            await session.commit()
            notify_sync_workers()
            _publish_mcp_graph_change(
                workflow,
                "rename_node",
                user,
                node_id=node_id,
                patch=patch,
            )
            return {
                "workflow_id": workflow.id,
                "graph_revision": workflow.graph_revision,
                "node_id": node_id,
                "label": label,
            }
    raise McpToolError(f"Node not found in draft graph: {node_id!r}")


async def _move_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
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
            bump_graph_revision(workflow)
            patch = {
                "type": "node_moved",
                "node_id": node_id,
                "position": pos,
            }
            _record_mcp_graph_revision(session, workflow, "move_node", user, patch)
            await log_audit(
                session, "mcp_move_node", "workflow", workflow.id, workflow.name,
                actor_id=user.id if user else None,
                actor_email=user.email if user else None,
            )
            await enqueue_github_push(session, workflow, "mcp")
            await session.commit()
            notify_sync_workers()
            _publish_mcp_graph_change(
                workflow,
                "move_node",
                user,
                node_id=node_id,
                patch=patch,
            )
            return {
                "workflow_id": workflow.id,
                "graph_revision": workflow.graph_revision,
                "node_id": node_id,
                "position": pos,
            }
    raise McpToolError(f"Node not found in draft graph: {node_id!r}")


async def _create_code_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "create_code_node", "add a node that runs new code")
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
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
    bump_graph_revision(workflow)
    patch = {"type": "node_added", "node": node_patch_snapshot(entry)}
    _record_mcp_graph_revision(session, workflow, "create_code_node", user, patch)
    await log_audit(
        session, "mcp_create_code_node", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await enqueue_github_push(session, workflow, "mcp")
    await session.commit()
    notify_sync_workers()
    _publish_mcp_graph_change(
        workflow,
        "create_code_node",
        user,
        node_id=node_id,
        patch=patch,
    )
    return {
        "workflow_id": workflow.id,
        "graph_revision": workflow.graph_revision,
        "node_id": node_id,
        "node_count": len(nodes),
    }


async def _update_code(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "update_code", "replace the code a workflow node runs")
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    _check_expected_graph_revision(workflow, args)
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
            bump_graph_revision(workflow)
            patch = {
                "type": "node_updated",
                "node_id": node_id,
                "param_keys": ["code"],
            }
            _record_mcp_graph_revision(session, workflow, "update_code", user, patch)
            await log_audit(
                session, "mcp_update_code", "workflow", workflow.id, workflow.name,
                actor_id=user.id if user else None,
                actor_email=user.email if user else None,
            )
            await enqueue_github_push(session, workflow, "mcp")
            await session.commit()
            notify_sync_workers()
            _publish_mcp_graph_change(
                workflow,
                "update_code",
                user,
                node_id=node_id,
                patch=patch,
            )
            return {
                "workflow_id": workflow.id,
                "graph_revision": workflow.graph_revision,
                "node_id": node_id,
            }
    raise McpToolError(f"Node not found in draft graph: {node_id!r}")


async def _retry_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    from app.routers.runs import retry_from_failure as _retry_route

    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise McpToolError("run_id is required.")
    # Retrying starts an execution, so it needs the same allowlist check as
    # run_workflow — otherwise any run id is a way around the opt-in.
    run = await session.scalar(select(Run).where(Run.id == run_id))
    if run is None:
        raise McpToolError(f"Run not found: {run_id}")
    workflow = await session.scalar(select(Workflow).where(Workflow.id == run.workflow_id))
    if workflow is not None:
        assert_workflow_runnable_over_mcp(workflow)
    try:
        result = await _retry_route(run_id, session)
    except Exception as exc:
        raise McpToolError(str(exc)) from exc
    return {"new_run_id": result.run_id, "retried_from": run_id}


async def _list_environments(session: AsyncSession, user: User | None, args: dict) -> Any:
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


def _dedupe_packages(packages: list[str]) -> list[str]:
    from nodyra.packages import canonical_package_name

    deduped: dict[str, str] = {}
    for raw in packages:
        spec = str(raw or "").strip()
        if spec:
            deduped[canonical_package_name(spec)] = spec
    return list(deduped.values())


def _bounded_int_arg(
    args: dict,
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise McpToolError(f"{name} must be an integer.") from exc
    return max(minimum, min(value, maximum))


async def _environment_info(
    session: AsyncSession,
    env: Environment,
    build_job: EnvironmentBuildJob | None = None,
) -> dict[str, Any]:
    from app.routers.environments import _pool_name, _to_info

    return _to_info(
        env,
        await _pool_name(session, env.runner_pool_id),
        build_job,
    ).model_dump(mode="json")


async def _create_environment(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "create_environment", "create and build an execution environment")
    from fastapi import HTTPException

    from app.routers.environments import _validate_pool, _validate_pool_ref
    from app.schemas import EnvironmentCreate
    from app.services.isolation import validate_pool_assignment
    from app.services.licensing import enforce_resource_cap
    from app.tenancy import active_org_id

    try:
        body = EnvironmentCreate(
            name=str(args.get("name") or "").strip(),
            python_version=str(args.get("python_version") or "3.12"),
            packages=_dedupe_packages(list(args.get("packages") or [])),
            description=str(args.get("description") or ""),
            runner_pool_size=int(args.get("runner_pool_size") or 1),
            runner_pool_max=args.get("runner_pool_max"),
            runner_pool_id=args.get("runner_pool_id"),
            backend=str(args.get("backend") or "venv"),
            backend_config=args.get("backend_config") or {},
            interpreter=str(args.get("interpreter") or "cpython"),
            runtime_flags=args.get("runtime_flags") or {},
        )
        if body.backend not in {"venv", "conda", "pixi"}:
            raise McpToolError("backend must be one of: venv, conda, pixi")
        _validate_pool(body.runner_pool_size, body.runner_pool_max)
        await _validate_pool_ref(session, body.runner_pool_id)
        await validate_pool_assignment(session, active_org_id(), body.runner_pool_id)
        await enforce_resource_cap(session, "environments")
    except HTTPException as exc:
        raise McpToolError(str(exc.detail)) from exc
    except ValidationError as exc:
        raise McpToolError(f"Invalid environment request: {exc.errors()[:5]}") from exc
    except ValueError as exc:
        raise McpToolError(str(exc)) from exc

    env = Environment(
        name=body.name,
        python_version=body.python_version,
        packages=body.packages,
        description=body.description,
        runner_pool_size=body.runner_pool_size,
        runner_pool_max=body.runner_pool_max,
        runner_pool_id=body.runner_pool_id,
        backend=body.backend,
        backend_config=body.backend_config,
        interpreter=body.interpreter,
        runtime_flags=dict(body.runtime_flags),
        status="pending",
    )
    session.add(env)
    await log_audit(
        session,
        "mcp_create_environment",
        "environment",
        detail=body.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.flush()
    build_job = await enqueue_environment_build(
        session,
        env,
        reason="mcp_create_environment",
        requested_by=user,
    )
    await session.commit()
    await session.refresh(env)
    await session.refresh(build_job)
    await notify_environment_build_workers()
    payload = await _environment_info(session, env, build_job)
    payload["build_started"] = True
    return payload


async def _load_environment(session: AsyncSession, env_id: str) -> Environment:
    if not env_id:
        raise McpToolError("environment_id is required.")
    env = await session.get(Environment, env_id)
    if env is None:
        raise McpToolError(f"Environment not found: {env_id}")
    return env


async def _set_environment_packages(
    session: AsyncSession,
    user: User | None,
    args: dict,
) -> Any:
    _require_explicit_mcp_approval(args, "set_environment_packages", "replace an environment package set")
    env = await _load_environment(session, str(args.get("environment_id") or ""))
    packages = args.get("packages")
    if not isinstance(packages, list):
        raise McpToolError("packages must be an array of package specs.")
    next_packages = _dedupe_packages(packages)
    changed = next_packages != list(env.packages or [])
    build_job: EnvironmentBuildJob | None = None
    if changed:
        env.packages = next_packages
        await log_audit(
            session,
            "mcp_set_environment_packages",
            "environment",
            env.id,
            env.name,
            actor_id=user.id if user else None,
            actor_email=user.email if user else None,
        )
        build_job = await enqueue_environment_build(
            session,
            env,
            reason="mcp_set_environment_packages",
            requested_by=user,
        )
        await session.commit()
        await session.refresh(env)
        await session.refresh(build_job)
        await notify_environment_build_workers()
    payload = await _environment_info(session, env, build_job)
    payload["build_started"] = changed
    return payload


async def _add_environment_package(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "add_environment_package", "install a package and rebuild an environment")
    env = await _load_environment(session, str(args.get("environment_id") or ""))
    package = str(args.get("package") or "").strip()
    if not package:
        raise McpToolError("package is required.")
    next_packages = _dedupe_packages([*(env.packages or []), package])
    return await _set_environment_packages(
        session,
        user,
        {
            "environment_id": env.id,
            "packages": next_packages,
            "approved_by_user": args.get("approved_by_user"),
        },
    )


async def _remove_environment_package(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "remove_environment_package", "remove a package and rebuild an environment")
    env = await _load_environment(session, str(args.get("environment_id") or ""))
    package = str(args.get("package") or "").strip()
    if not package:
        raise McpToolError("package is required.")
    from nodyra.packages import canonical_package_name

    target = canonical_package_name(package)
    next_packages = [
        existing
        for existing in env.packages or []
        if canonical_package_name(existing) != target
    ]
    return await _set_environment_packages(
        session,
        user,
        {
            "environment_id": env.id,
            "packages": next_packages,
            "approved_by_user": args.get("approved_by_user"),
        },
    )


async def _rebuild_environment(session: AsyncSession, user: User | None, args: dict) -> Any:
    _require_explicit_mcp_approval(args, "rebuild_environment", "rebuild an execution environment")
    env = await _load_environment(session, str(args.get("environment_id") or ""))
    await log_audit(
        session,
        "mcp_rebuild_environment",
        "environment",
        env.id,
        env.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    build_job = await enqueue_environment_build(
        session,
        env,
        reason="mcp_rebuild_environment",
        requested_by=user,
    )
    await session.commit()
    await session.refresh(env)
    await session.refresh(build_job)
    await notify_environment_build_workers()
    payload = await _environment_info(session, env, build_job)
    payload["build_started"] = True
    return payload


def _environment_build_job_payload(job: EnvironmentBuildJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "environment_id": job.environment_id,
        "reason": job.reason,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "package_snapshot": list(job.package_snapshot or []),
        "packages_hash": job.packages_hash,
        "python_version": job.python_version,
        "backend": job.backend,
        "last_error": job.last_error,
        "requested_by_email": job.requested_by_email,
        "lease_owner": job.lease_owner,
        "lease_expires_at": str(job.lease_expires_at) if job.lease_expires_at else None,
        "available_at": str(job.available_at),
        "started_at": str(job.started_at) if job.started_at else None,
        "finished_at": str(job.finished_at) if job.finished_at else None,
        "created_at": str(job.created_at),
        "updated_at": str(job.updated_at),
    }


async def _list_environment_build_jobs(
    session: AsyncSession,
    user: User | None,
    args: dict,
) -> Any:
    env = await _load_environment(session, str(args.get("environment_id") or ""))
    limit = _bounded_int_arg(args, "limit", default=20, minimum=1, maximum=100)
    rows = (
        await session.scalars(
            select(EnvironmentBuildJob)
            .where(EnvironmentBuildJob.environment_id == env.id)
            .order_by(EnvironmentBuildJob.created_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "environment_id": env.id,
        "build_jobs": [_environment_build_job_payload(job) for job in rows],
    }


async def _get_environment_build_job(
    session: AsyncSession,
    user: User | None,
    args: dict,
) -> Any:
    job_id = str(args.get("build_job_id") or "").strip()
    if not job_id:
        raise McpToolError("build_job_id is required.")
    stmt = select(EnvironmentBuildJob).where(EnvironmentBuildJob.id == job_id)
    environment_id = str(args.get("environment_id") or "").strip()
    if environment_id:
        stmt = stmt.where(EnvironmentBuildJob.environment_id == environment_id)
    job = await session.scalar(stmt)
    if job is None:
        raise McpToolError(f"Environment build job not found: {job_id}")
    return _environment_build_job_payload(job)


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
    publish_workflow_event(
        workflow,
        WORKFLOW_UPDATED,
        origin="mcp",
        operation="set_error_handler",
        actor=user,
    )
    return {"workflow_id": workflow.id, "error_workflow_id": error_workflow_id}


async def _enable_mcp_tool(session: AsyncSession, user: User | None, args: dict) -> Any:
    import re

    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    tool_name = str(args.get("tool_name") or "").strip()
    description = str(args.get("description") or "").strip() or None
    parameters_schema = _validate_mcp_parameters_schema(args.get("parameters_schema"))
    if tool_name and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool_name):
        raise McpToolError("tool_name must match [A-Za-z0-9_-]{1,64}.")
    effective_name = tool_name or workflow.mcp_tool_name or workflow_tool_name(workflow)
    if effective_name in {tool.name for tool in STATIC_TOOLS}:
        raise McpToolError(f"Tool name {effective_name!r} is reserved by Nodyra.")
    for other in await _mcp_enabled_workflows(session):
        if other.id != workflow.id and workflow_tool_name(other) == effective_name:
            raise McpToolError(
                f"Tool name {effective_name!r} is already used by workflow {other.id}."
            )
    workflow.mcp_enabled = True
    if tool_name:
        workflow.mcp_tool_name = tool_name
    if description:
        workflow.mcp_description = description
    if "parameters_schema" in args:
        workflow.mcp_parameters_schema = parameters_schema
    await log_audit(
        session, "mcp_enable_mcp_tool", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise McpToolError("Tool name is already used in this organization.") from exc
    notify_sync_workers()
    publish_workflow_event(
        workflow,
        WORKFLOW_UPDATED,
        origin="mcp",
        operation="enable_mcp_tool",
        actor=user,
    )
    return {
        "workflow_id": workflow.id,
        "mcp_enabled": True,
        "tool_name": workflow_tool_name(workflow),
        "description": workflow.mcp_description,
        "parameters_schema": workflow.mcp_parameters_schema,
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
    publish_workflow_event(
        workflow,
        WORKFLOW_UPDATED,
        origin="mcp",
        operation="disable_mcp_tool",
        actor=user,
    )
    return {"workflow_id": workflow.id, "mcp_enabled": False}


async def _update_workflow_settings(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    from app.routers.workflows import update_workflow
    from app.schemas import WorkflowUpdate

    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    allowed = {
        "environment_id", "default_runner_pool_id", "error_workflow_id",
        "error_alerts", "allow_concurrent", "execution_mode",
        "sandbox_resources",
        "run_timeout_seconds", "artifact_retention_days", "folder_id",
        "mcp_description", "mcp_parameters_schema",
    }
    values = {key: value for key, value in args.items() if key in allowed}
    if "execution_mode" in values and values["execution_mode"] not in VALID_EXECUTION_MODES:
        raise McpToolError(f"execution_mode must be one of {VALID_EXECUTION_MODES}.")
    if "sandbox_resources" in values and values["sandbox_resources"] is not None:
        try:
            values["sandbox_resources"] = validate_sandbox_resources(
                values["sandbox_resources"]
            )
        except ValueError as exc:
            raise McpToolError(str(exc)) from exc
    if "mcp_parameters_schema" in values:
        values["mcp_parameters_schema"] = _validate_mcp_parameters_schema(
            values["mcp_parameters_schema"]
        )
    if not values:
        raise McpToolError("At least one workflow setting must be supplied.")
    result = await update_workflow(
        workflow_id, WorkflowUpdate(**values), session, user
    )
    return result.model_dump(mode="json")


async def _get_workflow_version(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    version_number = args.get("version")
    version_id = str(args.get("version_id") or "").strip()
    if version_number is None and not version_id:
        raise McpToolError("version or version_id is required.")
    stmt = select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id)
    if version_id:
        stmt = stmt.where(WorkflowVersion.id == version_id)
    else:
        stmt = stmt.where(WorkflowVersion.version == int(version_number))
    version = await session.scalar(stmt)
    if version is None:
        raise McpToolError("Workflow version not found.")
    return {
        "workflow_id": workflow.id,
        "version_id": version.id,
        "version": version.version,
        "notes": version.notes,
        "created_at": str(version.created_at),
        "graph": version.graph or EMPTY_GRAPH,
    }


async def _diff_workflow_versions(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    from_version = args.get("from_version")
    if not isinstance(from_version, int):
        raise McpToolError("from_version must be an integer.")
    source = await session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version == from_version,
        )
    )
    if source is None:
        raise McpToolError(f"Version {from_version} not found.")
    to_version = args.get("to_version")
    if to_version is None:
        target_graph = _draft_graph(workflow)
        target_label = "draft"
    else:
        if not isinstance(to_version, int):
            raise McpToolError("to_version must be an integer.")
        target = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == to_version,
            )
        )
        if target is None:
            raise McpToolError(f"Version {to_version} not found.")
        target_graph = target.graph or EMPTY_GRAPH
        target_label = f"v{to_version}"
    before = json.dumps(source.graph or EMPTY_GRAPH, indent=2, sort_keys=True).splitlines()
    after = json.dumps(target_graph, indent=2, sort_keys=True).splitlines()
    patch = "\n".join(
        difflib.unified_diff(
            before, after, fromfile=f"v{from_version}", tofile=target_label, lineterm=""
        )
    )
    return {
        "workflow_id": workflow.id,
        "from": from_version,
        "to": target_label,
        "changed": bool(patch),
        "diff": patch[:20000],
        "truncated": len(patch) > 20000,
    }


async def _get_node_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "").strip()
    node_id = str(args.get("node_id") or "").strip()
    if not run_id or not node_id:
        raise McpToolError("run_id and node_id are required.")
    if await session.scalar(select(Run).where(Run.id == run_id)) is None:
        raise McpToolError(f"Run not found: {run_id}")
    rows = list(
        (
            await session.scalars(
                select(NodeRun)
                .where(NodeRun.run_id == run_id, NodeRun.node_id == node_id)
                .order_by(NodeRun.id)
                .limit(100)
            )
        ).all()
    )
    if not rows:
        raise McpToolError(f"Node run not found: {node_id}")
    return {
        "run_id": run_id,
        "node_id": node_id,
        "attempts": [
            {
                "status": row.status,
                "iteration_path": row.iteration_path,
                "duration_ms": row.duration_ms,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                # Resolve offloaded-output markers before transport (OS-1).
                "output": _truncated(resolve_ref(row.output)),
                "error": row.error,
                "logs": _truncated(row.logs),
                "debug": _truncated(row.debug),
            }
            for row in rows
        ],
    }


async def _list_run_approvals(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    from app.models import RunApproval

    run_id = str(args.get("run_id") or "").strip()
    status_filter = str(args.get("status") or "").strip()
    limit = _bounded_int_arg(args, "limit", default=50, minimum=1, maximum=200)
    stmt = (
        select(RunApproval)
        .join(Run, Run.id == RunApproval.run_id)
        .order_by(RunApproval.requested_at.desc())
        .limit(limit)
    )
    if run_id:
        stmt = stmt.where(RunApproval.run_id == run_id)
    if status_filter:
        stmt = stmt.where(RunApproval.status == status_filter)
    rows = list((await session.scalars(stmt)).all())
    return {
        "approvals": [
            {
                "id": row.id,
                "run_id": row.run_id,
                "status": row.status,
                "node_id": row.node_id,
                "tool_name": row.tool_name,
                "arguments": _truncated(row.arguments),
                "message": row.message,
                "requested_at": str(row.requested_at),
                "resolved_at": str(row.resolved_at) if row.resolved_at else None,
                "reason": row.reason,
            }
            for row in rows
        ]
    }


async def _resolve_run_approval(
    session: AsyncSession, user: User | None, args: dict
) -> Any:
    _require_explicit_mcp_approval(args, "resolve_run_approval", "resolve a pending human approval")
    from app.routers.runs import decide_run_approval
    from app.schemas import RunApprovalDecisionRequest

    run_id = str(args.get("run_id") or "").strip()
    approval_id = str(args.get("approval_id") or "").strip()
    decision = str(args.get("decision") or "").strip()
    if not run_id or not approval_id:
        raise McpToolError("run_id and approval_id are required.")
    result = await decide_run_approval(
        run_id,
        approval_id,
        RunApprovalDecisionRequest(
            decision=decision,
            reason=str(args.get("reason") or ""),
            resolved_by=user.email if user else "mcp",
        ),
        session,
    )
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Static tool list
# ---------------------------------------------------------------------------

STATIC_TOOLS: list[McpTool] = [
    McpTool(
        name="list_workflows",
        description=(
            "List Nodyra workflows with id, name, active state and node count. "
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
        name="get_workflow_authoring_guide",
        description=(
            "Production workflow-building guide for LLM clients. Returns the "
            "recommended tool sequence, graph shape, trigger recipes, schedule "
            "rules, production checklist, and common mistakes to avoid."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Optional natural-language workflow goal to echo in the guide.",
                },
                "detail": {
                    "type": "string",
                    "enum": ["compact", "standard", "full"],
                    "description": "How much guidance to return. Default: standard.",
                },
            },
        },
        permission=None,
        handler=_get_workflow_authoring_guide,
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
        name="get_node_contracts",
        description=(
            "Return compact LLM-ready contracts for node types: purpose, params, "
            "ports, requirements, graph node shape, examples, and node-specific "
            "pitfalls. Use this before assembling workflow graphs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "node_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional exact node type ids to return.",
                },
                "category": {"type": "string"},
                "search": {"type": "string"},
                "include_examples": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        permission=None,
        handler=_get_node_contracts,
    ),
    McpTool(
        name="search_node_catalog",
        description=(
            "Search node types by query, category, or required package. Returns node "
            "ports and package requirements so agents can choose built-ins before "
            "creating custom code."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "package": {"type": "string", "description": "Filter to nodes requiring this Python package."},
                "include_ports": {"type": "boolean", "default": True},
                "include_params": {"type": "boolean", "default": False},
                "include_deprecated": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
        permission=None,
        handler=_search_node_catalog,
    ),
    McpTool(
        name="get_node_schema",
        description="Alias for get_node_type. Returns the full manifest/schema for one node type.",
        input_schema={
            "type": "object",
            "properties": {"node_type": {"type": "string"}},
            "required": ["node_type"],
        },
        permission=None,
        handler=_get_node_schema,
    ),
    McpTool(
        name="suggest_node_config",
        description=(
            "Generate a starter node object for a node type, including placeholder "
            "required params, ports, and package requirements. Replace placeholders "
            "before applying it to a workflow."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "node_type": {"type": "string"},
                "node_id": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "include_optional_defaults": {"type": "boolean"},
            },
            "required": ["node_type"],
        },
        permission=None,
        handler=_suggest_node_config,
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
            "status values: running, queued, waiting, success, error, timed_out, cancelled."
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
                "sandbox": {
                    "type": "boolean",
                    "description": (
                        "Run in a disposable hardened container regardless of "
                        "the workflow's execution mode."
                    ),
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
        description=(
            "Aggregate stats for a workflow: total runs, success/error counts "
            "(including timed_out), last run time."
        ),
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
            "properties": {
                "name": {"type": "string"},
                "execution_mode": {
                    "type": "string",
                    "enum": list(VALID_EXECUTION_MODES),
                    "description": "Workflow isolation mode: inherit, sandboxed, or standard.",
                },
                "sandbox_resources": {
                    "type": "object",
                    "description": (
                        "Sandbox resource requests: memory_mb, cpu, tmpfs_mb; "
                        "clamped to deployment ceilings."
                    ),
                },
            },
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
            "properties": {
                "graph": {"type": "object"},
                "require_trigger": {"type": "boolean", "default": True},
            },
            "required": ["graph"],
        },
        permission=None,
        handler=_validate_graph,
    ),
    McpTool(
        name="validate_workflow_graph",
        description=(
            "Validate a workflow's draft graph, or a provided graph, without saving. "
            "Also returns node package requirements and packages missing from the "
            "workflow's current environment."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "graph": {"type": "object"},
                "require_trigger": {"type": "boolean", "default": True},
            },
        },
        permission=None,
        handler=_validate_workflow_graph,
    ),
    McpTool(
        name="preview_workflow_patch",
        description=(
            "Preview an atomic workflow graph patch without saving. Operations support "
            "add_node, update_node, move_node, rename_node, remove_node, add_edge, "
            "and remove_edge. Returns validation and package gaps."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "operations": {"type": "array", "items": {"type": "object"}},
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
                "require_trigger": {"type": "boolean", "default": True},
                "include_graph": {"type": "boolean", "default": False},
            },
            "required": ["workflow_id", "operations"],
        },
        permission=None,
        handler=_preview_workflow_patch,
    ),
    McpTool(
        name="apply_workflow_patch",
        description=(
            "Apply an atomic workflow graph patch to the draft. The final graph is "
            "validated once, graph_revision increments once, live canvas subscribers "
            "receive one graph_changed event, and one revision/audit entry is written."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "operations": {"type": "array", "items": {"type": "object"}},
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
                "require_trigger": {"type": "boolean", "default": True},
                "include_graph": {"type": "boolean", "default": False},
            },
            "required": ["workflow_id", "operations"],
        },
        permission="workflow:write",
        handler=_apply_workflow_patch,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
        name="list_workflow_revisions",
        description=(
            "List recent draft graph revisions for a workflow, newest first. "
            "Use this to understand what changed and which graph_revision to pass "
            "as expected_graph_revision before editing."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["workflow_id"],
        },
        permission=None,
        handler=_list_workflow_revisions,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
                "workflow_version_id": {"type": "string", "description": "Published version id to pin."},
                "approve_unsafe_nodes": {"type": "boolean", "description": "Explicitly acknowledge unsafe nodes when policy requires it."},
            },
            "required": ["workflow_id", "name"],
        },
        permission="deployment:write",
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
        permission="deployment:write",
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
                "approve_unsafe_nodes": {"type": "boolean"},
            },
            "required": ["schedule_id", "active"],
        },
        permission="deployment:write",
        handler=_toggle_schedule,
    ),
    McpTool(
        name="update_schedule",
        description="Update a schedule's timing, parameters, pinned version, or active state.",
        input_schema={
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string"},
                "name": {"type": "string"},
                "schedule_cron": {"type": "string"},
                "schedule_interval": {"type": "string"},
                "schedule_every": {"type": "integer", "minimum": 1},
                "schedule_tz": {"type": "string"},
                "default_parameters": {"type": "object"},
                "active": {"type": "boolean"},
                "workflow_version_id": {"type": "string"},
                "approve_unsafe_nodes": {"type": "boolean"},
            },
            "required": ["schedule_id"],
        },
        permission="deployment:write",
        handler=_update_schedule,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
                "expected_graph_revision": EXPECTED_GRAPH_REVISION_SCHEMA,
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
        name="create_environment",
        description=(
            "Create a Python execution environment and start building it. Use when a "
            "workflow needs packages that are not appropriate for the current env. "
            "Optionally select an accelerated interpreter: 'cpython-ft' (free-threaded, "
            "3.13/3.14, venv backend only) or 'pypy' (3.10/3.11, venv backend only), and "
            "opt into per-worker runtime_flags ({'jit': bool, 'lazy_imports': bool}, "
            "CPython-only; jit is rejected for pypy since PyPy always JIT-compiles)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "python_version": {"type": "string", "description": "Supported versions include 3.12, 3.13, 3.14."},
                "packages": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
                "backend": {"type": "string", "enum": ["venv", "conda", "pixi"]},
                "backend_config": {"type": "object"},
                "runner_pool_size": {"type": "integer", "minimum": 0},
                "runner_pool_max": {"type": ["integer", "null"], "minimum": 1},
                "runner_pool_id": {"type": ["string", "null"]},
                "interpreter": {
                    "type": "string",
                    "enum": ["cpython", "cpython-ft", "pypy"],
                    "description": (
                        "Interpreter implementation. cpython-ft (free-threaded) and pypy "
                        "require backend=venv and a minor-only python_version."
                    ),
                },
                "runtime_flags": {
                    "type": "object",
                    "description": "Optional {'jit': bool, 'lazy_imports': bool} spawn-time flags.",
                },
            },
            "required": ["name"],
        },
        permission="environment:write",
        handler=_create_environment,
    ),
    McpTool(
        name="add_environment_package",
        description=(
            "Add one Python package spec to an environment and start a rebuild. "
            "Package specs may include version pins, e.g. pandas==2.2.2."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "environment_id": {"type": "string"},
                "package": {"type": "string"},
            },
            "required": ["environment_id", "package"],
        },
        permission="environment:write",
        handler=_add_environment_package,
    ),
    McpTool(
        name="set_environment_packages",
        description="Replace an environment's package list and start a rebuild if it changed.",
        input_schema={
            "type": "object",
            "properties": {
                "environment_id": {"type": "string"},
                "packages": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["environment_id", "packages"],
        },
        permission="environment:write",
        handler=_set_environment_packages,
    ),
    McpTool(
        name="remove_environment_package",
        description="Remove one package from an environment by canonical package name and start a rebuild.",
        input_schema={
            "type": "object",
            "properties": {
                "environment_id": {"type": "string"},
                "package": {"type": "string"},
            },
            "required": ["environment_id", "package"],
        },
        permission="environment:write",
        handler=_remove_environment_package,
    ),
    McpTool(
        name="rebuild_environment",
        description="Mark an environment pending and start a rebuild with its current packages.",
        input_schema={
            "type": "object",
            "properties": {"environment_id": {"type": "string"}},
            "required": ["environment_id"],
        },
        permission="environment:write",
        handler=_rebuild_environment,
    ),
    McpTool(
        name="list_environment_build_jobs",
        description="List recent durable build jobs for an environment, newest first.",
        input_schema={
            "type": "object",
            "properties": {
                "environment_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["environment_id"],
        },
        permission=None,
        handler=_list_environment_build_jobs,
    ),
    McpTool(
        name="get_environment_build_job",
        description="Get durable build status, attempts, lease, and error details for one environment build job.",
        input_schema={
            "type": "object",
            "properties": {
                "build_job_id": {"type": "string"},
                "environment_id": {"type": "string"},
            },
            "required": ["build_job_id"],
        },
        permission=None,
        handler=_get_environment_build_job,
    ),
    McpTool(
        name="list_credentials",
        description=(
            "List all credential sets by name and type (no secret values returned). "
            "Use to discover what credentials are available to reference in node params."
        ),
        input_schema={"type": "object", "properties": {}},
        permission="credential:read",
        handler=_list_credentials,
    ),
    McpTool(
        name="set_error_handler",
        description=(
            "Set or clear the error-handler workflow for a workflow. "
            "When a run fails, Nodyra will trigger error_workflow_id with the error details. "
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
                "parameters_schema": {
                    "type": "object",
                    "description": "JSON Schema for the workflow tool's input object.",
                },
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
    McpTool(
        name="update_workflow_settings",
        description="Update execution, environment, retention, error-handler, folder, and MCP schema settings.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "environment_id": {"type": ["string", "null"]},
                "default_runner_pool_id": {"type": ["string", "null"]},
                "error_workflow_id": {"type": ["string", "null"]},
                "error_alerts": {"type": "object"},
                "allow_concurrent": {"type": "boolean"},
                "execution_mode": {
                    "type": "string",
                    "enum": list(VALID_EXECUTION_MODES),
                },
                "sandbox_resources": {
                    "type": ["object", "null"],
                    "description": (
                        "Sandbox resource requests: memory_mb, cpu, tmpfs_mb; "
                        "clamped to deployment ceilings."
                    ),
                },
                "run_timeout_seconds": {"type": ["number", "null"], "minimum": 0},
                "artifact_retention_days": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "maximum": 3650,
                },
                "folder_id": {"type": ["string", "null"]},
                "mcp_description": {"type": ["string", "null"]},
                "mcp_parameters_schema": {"type": ["object", "null"]},
            },
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_update_workflow_settings,
    ),
    McpTool(
        name="get_workflow_version",
        description="Read the immutable graph and metadata for one published workflow version.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "version": {"type": "integer"},
                "version_id": {"type": "string"},
            },
            "required": ["workflow_id"],
        },
        permission=None,
        handler=_get_workflow_version,
    ),
    McpTool(
        name="diff_workflow_versions",
        description="Return a unified JSON diff between a published version and another version or the draft.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "from_version": {"type": "integer"},
                "to_version": {"type": "integer", "description": "Omit to compare with the draft."},
            },
            "required": ["workflow_id", "from_version"],
        },
        permission=None,
        handler=_diff_workflow_versions,
    ),
    McpTool(
        name="get_node_run",
        description="Get detailed logs, debug data, timing, output, and errors for one node in a run.",
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["run_id", "node_id"],
        },
        permission="workflow:run",
        handler=_get_node_run,
    ),
    McpTool(
        name="list_run_approvals",
        description="List pending or resolved AI tool approvals, optionally for one run.",
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "approved", "rejected"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
        permission="workflow:run",
        handler=_list_run_approvals,
    ),
    McpTool(
        name="resolve_run_approval",
        description="Approve or reject a pending AI tool call and resume the waiting run.",
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "approval_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["approve", "reject", "approve_all"]},
                "reason": {"type": "string"},
            },
            "required": ["run_id", "approval_id", "decision"],
        },
        permission="workflow:run",
        handler=_resolve_run_approval,
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


async def _mcp_enabled_workflows(
    session: AsyncSession,
    *,
    offset: int = 0,
    limit: int | None = None,
    after_updated_at: datetime | None = None,
    after_id: str | None = None,
) -> list[Workflow]:
    # No version eager-load: descriptors only need the mcp_* columns, and the
    # call path re-loads the chosen workflow (with versions) by id anyway.
    stmt = select(Workflow).where(Workflow.mcp_enabled.is_(True))
    if after_updated_at is not None and after_id is not None:
        # Keyset pagination is stable when rows are inserted or deleted between
        # requests. ``id`` is the deterministic tie-breaker for equal database
        # timestamps (common with SQLite and bulk imports).
        stmt = stmt.where(
            or_(
                Workflow.updated_at < after_updated_at,
                and_(
                    Workflow.updated_at == after_updated_at,
                    Workflow.id > after_id,
                ),
            )
        )
    stmt = stmt.order_by(Workflow.updated_at.desc(), Workflow.id.asc())
    if offset > 0:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = await session.scalars(stmt)
    return list(rows.all())


async def count_workflow_tool_descriptors(session: AsyncSession) -> int:
    total = await session.scalar(
        select(func.count()).select_from(Workflow).where(Workflow.mcp_enabled.is_(True))
    )
    return int(total or 0)


async def list_workflow_tool_descriptors(
    session: AsyncSession,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[dict]:
    out: list[dict] = []
    static_names = {t.name for t in STATIC_TOOLS}
    seen: set[str] = set()
    for wf in await _mcp_enabled_workflows(session, offset=offset, limit=limit):
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
                "description": wf.mcp_description or f"Run the Nodyra workflow '{wf.name}'.",
                "inputSchema": schema
                if isinstance(schema, dict) and schema
                else _PERMISSIVE_SCHEMA,
                "outputSchema": {"type": "object", "additionalProperties": True},
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
                "execution": {"taskSupport": "forbidden"},
            }
        )
    return out


async def list_workflow_tool_descriptor_page(
    session: AsyncSession,
    *,
    limit: int,
    offset: int = 0,
    after_updated_at: datetime | None = None,
    after_id: str | None = None,
) -> tuple[list[dict], tuple[datetime, str] | None, bool]:
    """Return one deterministic, mutation-safe dynamic-tool page.

    ``offset`` exists only to consume legacy cursors. Every cursor emitted by
    the current router carries the final row's ``(updated_at, id)`` keyset, so
    subsequent pages do not drift when another workflow is inserted or removed.
    Name collisions are rejected when MCP exposure is enabled; the defensive
    descriptor filter remains for legacy/corrupt rows.
    """
    if limit <= 0:
        return [], None, False
    rows = await _mcp_enabled_workflows(
        session,
        offset=offset,
        limit=limit + 1,
        after_updated_at=after_updated_at,
        after_id=after_id,
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    static_names = {tool.name for tool in STATIC_TOOLS}
    seen: set[str] = set()
    tools: list[dict] = []
    for workflow in page_rows:
        name = workflow_tool_name(workflow)
        if name in static_names or name in seen:
            logger.error(
                "MCP tool name invariant violated: '%s' (workflow %s) is not "
                "unique; disable or rename the conflicting workflow",
                name,
                workflow.id,
            )
            continue
        seen.add(name)
        schema = workflow.mcp_parameters_schema
        tools.append(
            {
                "name": name,
                "description": workflow.mcp_description
                or f"Run the Nodyra workflow '{workflow.name}'.",
                "inputSchema": schema
                if isinstance(schema, dict) and schema
                else _PERMISSIVE_SCHEMA,
                "outputSchema": {"type": "object", "additionalProperties": True},
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
                "execution": {"taskSupport": "forbidden"},
            }
        )
    anchor = (
        (page_rows[-1].updated_at, page_rows[-1].id) if page_rows else None
    )
    return tools, anchor, has_more


async def call_workflow_tool(
    session: AsyncSession, user: User | None, name: str, arguments: dict
) -> Any | None:
    """Dispatch a dynamic workflow tool by name; None when no workflow matches."""
    for wf in await _mcp_enabled_workflows(session):
        if workflow_tool_name(wf) == name:
            schema = wf.mcp_parameters_schema
            validate_tool_arguments(
                schema if isinstance(schema, dict) and schema else _PERMISSIVE_SCHEMA,
                arguments,
            )
            return await run_workflow_by_id(
                session,
                wf.id,
                parameters=arguments or None,
                wait_seconds=DEFAULT_WAIT_SECONDS,
                use_draft=False,
            )
    return None
