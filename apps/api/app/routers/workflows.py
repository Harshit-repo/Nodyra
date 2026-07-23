import asyncio
import contextlib
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, WebSocket, status
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.orm.attributes import set_committed_value
from starlette.websockets import WebSocketDisconnect

import nodyra_nodes  # noqa: F401 - registers built-in nodes
from app.config import settings
from app.db import SessionLocal, get_session
from app.models import (
    Environment,
    Folder,
    PinnedData,
    ProviderTriggerSubscription,
    Run,
    RunnerPool,
    User,
    Workflow,
    WorkflowCheck,
    WorkflowRevision,
    WorkflowVersion,
)
from app.schemas import (
    AiWorkflowDraftRequest,
    AiWorkflowDraftResponse,
    NodeTestRequest,
    NodeTestResponse,
    PageResponse,
    ProviderTriggerStatusCounts,
    ProviderTriggerSubscriptionInfo,
    WorkflowCheckInfo,
    WorkflowCheckRunResult,
    WorkflowChecksSaveRequest,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowPublishRequest,
    WorkflowPublishResponse,
    WorkflowRevisionInfo,
    WorkflowSummary,
    WorkflowUpdate,
    WorkflowVersionInfo,
)
from app.security import (
    _user_from_session_token,
    optional_current_user,
    require_permission,
    resolve_org_for,
)
from app.services import runner as runner_service
from app.services.ai_builder import build_workflow_draft
from app.services.artifacts import make_artifact_store
from app.services.audit import log_audit
from app.services.events import workflow_broker
from app.services.github_sync import enqueue_github_push
from app.services.github_sync_jobs import notify_sync_workers
from app.services.graph_utils import first_trigger_node, resolve_trigger_targets
from app.services.provider_triggers import sync_workflow_provider_triggers
from app.services.runtime_pool import _org_run_limits_for
from app.services.runtime_pool import pool as runtime_pool
from app.services.sandbox_policy import (
    VALID_EXECUTION_MODES,
    validate_sandbox_resources,
)
from app.services.workflow_events import (
    WORKFLOW_CREATED,
    WORKFLOW_DELETED,
    WORKFLOW_PUBLISHED,
    WORKFLOW_UPDATED,
    bump_graph_revision,
    publish_workflow_event,
    publish_workflow_graph_changed,
    record_workflow_revision,
)
from app.tenancy import current_org_id, run_as_org
from nodyra.context import artifact_store, org_run_limits
from nodyra.engine import execute
from nodyra.models import WorkflowGraph
from nodyra.sdk import register_module_functions, unregister_module
from nodyra.sdk import registry as node_registry
from nodyra.serialization import deserialize_value, serialize_value

logger = logging.getLogger("nodyra")

router = APIRouter(prefix="/workflows", tags=["workflows"])
ws_router = APIRouter(tags=["workflows"])

EMPTY_GRAPH: dict = {"nodes": [], "edges": []}

# Structural node types the engine resolves directly (inlined/expanded at plan
# time) rather than dispatching through a registry manifest. They never appear
# in ``node_registry.manifests()`` but are valid in a persisted graph.
STRUCTURAL_NODE_TYPES: frozenset[str] = frozenset({"meta_node"})


def _validate_requirement_lines(requirements: list[str] | None) -> list[str]:
    from packaging.requirements import InvalidRequirement, Requirement

    cleaned: list[str] = []
    for raw in requirements or []:
        line = str(raw).strip()
        if not line:
            continue
        try:
            Requirement(line)
        except InvalidRequirement as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Invalid requirement {line!r}: {exc}",
            ) from exc
        cleaned.append(line)
    return cleaned


def _validate_node_types(graph: dict | WorkflowGraph) -> None:
    """422 with a list of unknown node types — guards against typos that
    only surface at run-time with a confusing 'unknown node' engine error.

    Skips user-defined code-module types (``user:{id}:{fn}``) since those
    are registered dynamically when the workflow runs, not in the global
    registry visible here. Also skips structural types that the engine
    handles directly rather than via a registry manifest (e.g. ``meta_node``,
    which is inlined/expanded at plan time).
    """
    nodes = graph.nodes if isinstance(graph, WorkflowGraph) else graph.get("nodes", [])
    known = {m.id for m in node_registry.manifests()}
    unknown: set[str] = set()
    for n in nodes:
        t = n.type if hasattr(n, "type") else n.get("type")
        if (
            not t
            or t in known
            or t in STRUCTURAL_NODE_TYPES
            or t.startswith("user:")
        ):
            continue
        unknown.add(t)
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Unknown node types", "unknown": sorted(unknown)},
        )


async def _load(session: AsyncSession, workflow_id: str) -> Workflow:
    result = await session.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.versions))
        .execution_options(populate_existing=True)
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return workflow


def _latest(workflow: Workflow) -> WorkflowVersion | None:
    return workflow.versions[-1] if workflow.versions else None


def _draft_graph(workflow: Workflow) -> dict:
    if workflow.draft_graph is not None:
        return workflow.draft_graph
    latest = _latest(workflow)
    return latest.graph if latest is not None and latest.graph is not None else EMPTY_GRAPH


def _select_graph(workflow: Workflow, *, use_draft: bool) -> dict:
    if use_draft:
        return _draft_graph(workflow)
    latest = _latest(workflow)
    return latest.graph if latest is not None and latest.graph is not None else EMPTY_GRAPH


def _check_info(check: WorkflowCheck) -> WorkflowCheckInfo:
    return WorkflowCheckInfo(
        id=check.id,
        workflow_id=check.workflow_id,
        name=check.name,
        input_data=check.input_data or {},
        expected_outputs=check.expected_outputs or {},
        assertions=list(check.assertions or []),
        status=check.status,
        last_result=check.last_result,
        last_run_at=check.last_run_at,
        created_at=check.created_at,
        updated_at=check.updated_at,
    )


def _node_outputs_for_check(result) -> dict[str, dict[str, Any]]:
    return {
        node_id: serialize_value(node_result.outputs)
        for node_id, node_result in result.nodes.items()
    }


def _lookup_expected_output(
    node_outputs: dict[str, dict[str, Any]], key: str
) -> tuple[bool, Any]:
    if key in node_outputs:
        return True, node_outputs[key]
    if "." in key:
        node_id, port = key.split(".", 1)
        if node_id in node_outputs and port in node_outputs[node_id]:
            return True, node_outputs[node_id][port]
    if len(node_outputs) == 1:
        only_outputs = next(iter(node_outputs.values()))
        if key in only_outputs:
            return True, only_outputs[key]
    return False, None


def _evaluate_check_result(
    check: WorkflowCheck, result
) -> tuple[bool, list[str], dict[str, dict[str, Any]]]:
    status_text = str(result.status)
    node_outputs = _node_outputs_for_check(result)
    failures: list[str] = []

    for key, expected in (check.expected_outputs or {}).items():
        found, actual = _lookup_expected_output(node_outputs, str(key))
        if not found:
            failures.append(f"Expected output {key!r} was not produced.")
        elif actual != expected:
            failures.append(f"Expected {key!r} to be {expected!r}, got {actual!r}.")

    if not check.expected_outputs and not check.assertions and status_text != "success":
        failures.append(f"Expected run status 'success', got {status_text!r}.")

    for raw_assertion in check.assertions or []:
        assertion = str(raw_assertion).strip().lower()
        if not assertion:
            continue
        if "status" in assertion and "success" in assertion and status_text != "success":
            failures.append(f"Assertion failed: {raw_assertion}")

    return not failures and status_text == "success", failures, node_outputs


async def _execute_workflow_check(
    session: AsyncSession,
    workflow: Workflow,
    check: WorkflowCheck,
):
    graph_dict = _draft_graph(workflow)
    trigger = first_trigger_node(graph_dict, prefer_manual=True)
    trigger_id = trigger["id"] if isinstance(trigger, dict) else getattr(trigger, "id", None)
    targets = resolve_trigger_targets(graph_dict, trigger_id, None) if trigger_id else None
    cache = runner_service._seed_parameters(
        graph_dict,
        None,
        check.input_data or {},
        trigger_id=trigger_id,
    )
    ephemeral_run_id = f"workflow-check-{uuid4().hex}"
    prep = await runner_service._prepare_run_context(
        ephemeral_run_id,
        workflow.id,
        graph_dict,
        cache or None,
        workflow.org_id,
    )

    loaded_module_ids: list[str] = []
    artifact_token = artifact_store.set(
        make_artifact_store(
            ephemeral_run_id,
            org_id=workflow.org_id,
            max_bytes=prep.max_artifact_bytes,
            max_count=prep.max_artifacts_per_run,
        )
    )
    limits_token = org_run_limits.set(await _org_run_limits_for(workflow.org_id))
    try:
        for module in prep.workflow_modules:
            if not module.get("contents", "").strip():
                continue
            register_module_functions(
                module["id"],
                module["contents"],
                node_registry,
                include_undecorated=bool(module.get("include_undecorated")),
            )
            loaded_module_ids.append(module["id"])

        prepared_graph = WorkflowGraph.model_validate(prep.graph_dict)
        async with runtime_pool.global_slot():
            return await execute(
                prepared_graph,
                node_registry,
                cache=deserialize_value(prep.cache or {}),
                targets=targets,
                default_timeouts=runner_service._engine_default_timeouts(),
                max_node_output_bytes=prep.output_cap,
                process_isolator=runner_service.process_isolator,
            )
    finally:
        org_run_limits.reset(limits_token)
        artifact_store.reset(artifact_token)
        for module_id in loaded_module_ids:
            unregister_module(module_id, node_registry)


async def _run_and_persist_check(
    session: AsyncSession, workflow: Workflow, check: WorkflowCheck
) -> WorkflowCheckRunResult:
    result = await _execute_workflow_check(session, workflow, check)
    passed, failures, node_outputs = _evaluate_check_result(check, result)
    status_text = str(result.status)
    now = datetime.now(UTC)
    check.status = "passed" if passed else "failed"
    check.last_run_at = now
    check.updated_at = now
    check.last_result = serialize_value(
        {
            "passed": passed,
            "status": status_text,
            "failures": failures,
            "node_outputs": node_outputs,
        }
    )
    await session.flush()
    return WorkflowCheckRunResult(
        check=_check_info(check),
        passed=passed,
        status=status_text,
        failures=failures,
        node_outputs=node_outputs,
    )


def _node_exists(graph: WorkflowGraph, node_id: str) -> bool:
    return any(node.id == node_id for node in graph.nodes)


def _overlay_direct_inputs(
    graph: WorkflowGraph,
    node_id: str,
    cache: dict[str, dict[str, Any]],
    inputs: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not inputs:
        return cache

    incoming = [edge for edge in graph.edges if edge.target == node_id]
    available_ports = {edge.target_input for edge in incoming}
    unknown_ports = sorted(set(inputs) - available_ports)
    if unknown_ports:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Input override has no direct upstream edge for port(s): "
            + ", ".join(unknown_ports),
        )

    next_cache = {node: dict(outputs) for node, outputs in cache.items()}
    for edge in incoming:
        if edge.target_input not in inputs:
            continue
        upstream_outputs = dict(next_cache.get(edge.source) or {})
        upstream_outputs[edge.source_output] = inputs[edge.target_input]
        next_cache[edge.source] = upstream_outputs
    return next_cache


def _missing_direct_upstream(
    graph: WorkflowGraph,
    node_id: str,
    cache: dict[str, dict[str, Any]],
) -> list[str]:
    missing: list[str] = []
    for edge in graph.edges:
        if edge.target != node_id:
            continue
        upstream_outputs = cache.get(edge.source)
        if not isinstance(upstream_outputs, dict) or edge.source_output not in upstream_outputs:
            missing.append(
                f"{edge.target_input} <- {edge.source}.{edge.source_output}"
            )
    return missing


def _schedule_deployment_fields(graph: dict) -> dict[str, object] | None:
    """Return deployment schedule fields from the first schedule trigger."""
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "schedule_trigger":
            continue
        params = node.get("params") or {}
        if not isinstance(params, dict):
            return None
        try:
            every = max(int(params.get("every", 1) or 1), 1)
        except (TypeError, ValueError):
            every = 1
        return {
            "schedule_cron": str(params.get("cron") or "").strip(),
            "schedule_interval": str(params.get("interval") or "hours"),
            "schedule_every": every,
            "schedule_tz": str(params.get("tz") or ""),
        }
    return None


async def _sync_deployments_to_published_graph(
    session: AsyncSession,
    workflow_id: str,
    workflow_version_id: str,
    graph: dict,
) -> int:
    from app.models import Deployment

    deployments = (
        await session.scalars(select(Deployment).where(Deployment.workflow_id == workflow_id))
    ).all()
    schedule_fields = _schedule_deployment_fields(graph)
    for deployment in deployments:
        deployment.workflow_version_id = workflow_version_id
        if schedule_fields is not None:
            deployment.schedule_cron = str(schedule_fields["schedule_cron"])
            deployment.schedule_interval = str(schedule_fields["schedule_interval"])
            deployment.schedule_every = int(schedule_fields["schedule_every"])
            deployment.schedule_tz = str(schedule_fields["schedule_tz"])
    return len(deployments)


def _has_unpublished_changes(workflow: Workflow) -> bool:
    latest = _latest(workflow)
    published_graph = (
        latest.graph if latest is not None and latest.graph is not None else EMPTY_GRAPH
    )
    return _draft_graph(workflow) != published_graph


async def _global_env_id(session: AsyncSession) -> str | None:
    result = await session.scalars(
        select(Environment).where(Environment.is_global.is_(True)).limit(1)
    )
    env = result.first()
    return env.id if env is not None else None


async def _latest_run(session: AsyncSession, workflow_id: str) -> Run | None:
    return await session.scalar(
        select(Run).where(Run.workflow_id == workflow_id).order_by(Run.started_at.desc()).limit(1)
    )


async def _latest_runs(
    session: AsyncSession, workflow_ids: list[str]
) -> dict[str, Run]:
    """Batch-load the most recent run per workflow in a single query.

    Avoids the N+1 that one ``_latest_run`` call per listed workflow would
    cause. Uses a window function (``ROW_NUMBER`` partitioned by workflow,
    newest first) which both Postgres and modern SQLite support.
    """
    if not workflow_ids:
        return {}
    ranked = (
        select(
            Run,
            func.row_number()
            .over(
                partition_by=Run.workflow_id,
                order_by=Run.started_at.desc(),
            )
            .label("_rn"),
        )
        .where(Run.workflow_id.in_(workflow_ids))
        .subquery()
    )
    run_alias = aliased(Run, ranked)
    rows = (
        await session.scalars(
            select(run_alias).where(ranked.c._rn == 1)
        )
    ).all()
    return {run.workflow_id: run for run in rows}


def _empty_provider_trigger_counts() -> dict[str, int]:
    return {
        "total": 0,
        "active": 0,
        "activating": 0,
        "error": 0,
        "deleted": 0,
    }


async def _provider_trigger_counts(
    session: AsyncSession,
    workflow_ids: list[str],
) -> dict[str, ProviderTriggerStatusCounts]:
    if not workflow_ids:
        return {}
    raw: dict[str, dict[str, int]] = {
        workflow_id: _empty_provider_trigger_counts() for workflow_id in workflow_ids
    }
    rows = (
        await session.execute(
            select(
                ProviderTriggerSubscription.workflow_id,
                ProviderTriggerSubscription.status,
                func.count(),
            )
            .where(ProviderTriggerSubscription.workflow_id.in_(workflow_ids))
            .group_by(
                ProviderTriggerSubscription.workflow_id,
                ProviderTriggerSubscription.status,
            )
        )
    ).all()
    for workflow_id, row_status, count in rows:
        counts = raw.setdefault(str(workflow_id), _empty_provider_trigger_counts())
        status_key = str(row_status or "")
        counts["total"] += int(count)
        if status_key in counts:
            counts[status_key] += int(count)
    return {
        workflow_id: ProviderTriggerStatusCounts(**counts)
        for workflow_id, counts in raw.items()
    }


def _summary_from(
    workflow: Workflow,
    latest_run: Run | None,
    provider_trigger_counts: ProviderTriggerStatusCounts | None = None,
) -> WorkflowSummary:
    graph = _draft_graph(workflow)
    return WorkflowSummary(
        id=workflow.id,
        name=workflow.name,
        active=workflow.active,
        version=workflow.published_version,
        published_version=workflow.published_version,
        graph_revision=workflow.graph_revision,
        has_unpublished_changes=_has_unpublished_changes(workflow),
        node_count=len(graph.get("nodes", [])),
        environment_id=workflow.environment_id,
        error_workflow_id=workflow.error_workflow_id,
        last_run_id=latest_run.id if latest_run is not None else None,
        last_run_status=latest_run.status if latest_run is not None else None,
        last_run_started_at=latest_run.started_at if latest_run is not None else None,
        last_run_finished_at=latest_run.finished_at if latest_run is not None else None,
        provider_trigger_counts=(
            provider_trigger_counts or ProviderTriggerStatusCounts()
        ),
        folder_id=workflow.folder_id,
        updated_at=workflow.updated_at,
        created_at=workflow.created_at,
        github_sync_status=workflow.github_sync_status,
    )


async def _summary(session: AsyncSession, workflow: Workflow) -> WorkflowSummary:
    latest_run = await _latest_run(session, workflow.id)
    counts = await _provider_trigger_counts(session, [workflow.id])
    return _summary_from(workflow, latest_run, counts.get(workflow.id))


async def _detail(session: AsyncSession, workflow: Workflow) -> WorkflowDetail:
    counts = await _provider_trigger_counts(session, [workflow.id])
    return WorkflowDetail(
        id=workflow.id,
        name=workflow.name,
        active=workflow.active,
        version=workflow.published_version,
        published_version=workflow.published_version,
        graph_revision=workflow.graph_revision,
        has_unpublished_changes=_has_unpublished_changes(workflow),
        environment_id=workflow.environment_id,
        default_runner_pool_id=workflow.default_runner_pool_id,
        folder_id=workflow.folder_id,
        error_workflow_id=workflow.error_workflow_id,
        error_alerts=workflow.error_alerts or {},
        allow_concurrent=workflow.allow_concurrent,
        execution_mode=workflow.execution_mode,
        sandbox_resources=workflow.sandbox_resources,
        requirements=list(workflow.requirements or []),
        run_timeout_seconds=workflow.run_timeout_seconds,
        artifact_retention_days=workflow.artifact_retention_days,
        mcp_enabled=workflow.mcp_enabled,
        mcp_tool_name=workflow.mcp_tool_name,
        mcp_description=workflow.mcp_description,
        mcp_parameters_schema=workflow.mcp_parameters_schema,
        provider_trigger_counts=counts.get(
            workflow.id,
            ProviderTriggerStatusCounts(),
        ),
        graph=WorkflowGraph.model_validate(_draft_graph(workflow)),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        github_sync_status=workflow.github_sync_status,
    )


def _revision_info(revision: WorkflowRevision) -> WorkflowRevisionInfo:
    return WorkflowRevisionInfo(
        id=revision.id,
        workflow_id=revision.workflow_id,
        graph_revision=revision.graph_revision,
        origin=revision.origin,
        operation=revision.operation,
        summary=revision.summary,
        patch=revision.patch,
        actor_id=revision.actor_id,
        actor_email=revision.actor_email,
        created_at=revision.created_at,
    )


@router.get(
    "",
    response_model=PageResponse[WorkflowSummary],
    dependencies=[Depends(require_permission("workflow:read"))],
)
async def list_workflows(
    response: Response,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    response.headers["Cache-Control"] = "no-store"
    count = await session.scalar(select(func.count()).select_from(Workflow))
    result = await session.scalars(
        select(Workflow)
        .order_by(Workflow.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    workflows = result.all()

    # Load only the latest version per workflow (one IN query instead of
    # selectinload which pulls every historical version — T-04).
    if workflows:
        wf_ids = [w.id for w in workflows]
        # Use a window function to pick only the max version per workflow.
        max_ver_sq = (
            select(
                WorkflowVersion.workflow_id,
                func.max(WorkflowVersion.version).label("max_ver"),
            )
            .where(WorkflowVersion.workflow_id.in_(wf_ids))
            .group_by(WorkflowVersion.workflow_id)
            .subquery()
        )
        latest_versions_rows = (await session.scalars(
            select(WorkflowVersion)
            .join(
                max_ver_sq,
                (WorkflowVersion.workflow_id == max_ver_sq.c.workflow_id)
                & (WorkflowVersion.version == max_ver_sq.c.max_ver),
            )
        )).all()
        # Attach the single loaded version so relationship access works.
        # Use set_committed_value to bypass the lazy-load trigger that fires on
        # direct assignment (w.versions = ...) when outside a greenlet context.
        latest_by_id = {v.workflow_id: v for v in latest_versions_rows}
        for w in workflows:
            ver = latest_by_id.get(w.id)
            set_committed_value(w, "versions", [ver] if ver is not None else [])
    latest_by_wf = await _latest_runs(session, [w.id for w in workflows])
    provider_counts = await _provider_trigger_counts(session, [w.id for w in workflows])
    items = [
        _summary_from(w, latest_by_wf.get(w.id), provider_counts.get(w.id))
        for w in workflows
    ]
    return PageResponse(items=items, total=count or 0, limit=limit, offset=offset)


@router.post(
    "",
    response_model=WorkflowDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def create_workflow(
    body: WorkflowCreate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    workflow = Workflow(
        name=body.name,
        environment_id=await _global_env_id(session),
        draft_graph=dict(EMPTY_GRAPH),
        published_version=1,
    )
    workflow.versions.append(WorkflowVersion(version=1, graph=dict(EMPTY_GRAPH)))
    session.add(workflow)
    await log_audit(session, "create", "workflow", detail=body.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await enqueue_github_push(session, workflow, "ui")
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Workflow conflicts with an existing tenant-scoped value.",
        ) from exc
    notify_sync_workers()
    publish_workflow_event(
        workflow,
        WORKFLOW_CREATED,
        origin="ui",
        operation="create",
        actor=actor,
    )
    return await _detail(session, await _load(session, workflow.id))


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    _user: User | None = Depends(optional_current_user),
):
    return await _detail(session, await _load(session, workflow_id))


@router.get(
    "/{workflow_id}/revisions",
    response_model=list[WorkflowRevisionInfo],
    dependencies=[Depends(require_permission("workflow:read"))],
)
async def list_workflow_revisions(
    workflow_id: str,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    await _load(session, workflow_id)
    rows = (
        await session.scalars(
            select(WorkflowRevision)
            .where(WorkflowRevision.workflow_id == workflow_id)
            .order_by(WorkflowRevision.graph_revision.desc())
            .limit(limit)
        )
    ).all()
    return [_revision_info(row) for row in rows]


async def _workflow_ws_principal(websocket: WebSocket) -> tuple[User | None, str | None] | None:
    user: User | None = None
    token: str | None = None
    ticket = websocket.query_params.get("ticket", "")
    async with SessionLocal() as session:
        if ticket:
            from app.services.ws_ticket import consume_ticket

            user_id = await consume_ticket(ticket)
            if user_id is None:
                await websocket.close(code=1008)
                return None
            user = await session.get(User, user_id)
            if user is None:
                await websocket.close(code=1008)
                return None
        token = websocket.query_params.get("token", "")
        if not token:
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
        if not token:
            token = websocket.cookies.get(settings.session_cookie_name, "")
        if user is None and token:
            user = await _user_from_session_token(token, session)
            if user is None:
                await websocket.close(code=1008)
                return None
        if settings.auth_required and user is None:
            await websocket.close(code=1008)
            return None

        org_token = current_org_id.set(None)
        try:
            try:
                org_id = await resolve_org_for(
                    websocket.query_params.get("org_id"),
                    user,
                    session,
                )
            except HTTPException:
                await websocket.close(code=1008)
                return None
        finally:
            current_org_id.reset(org_token)
    return user, org_id


@ws_router.websocket("/ws/workflows/{workflow_id}")
async def workflow_events(websocket: WebSocket, workflow_id: str) -> None:
    principal = await _workflow_ws_principal(websocket)
    if principal is None:
        return
    _user, org_id = principal
    with run_as_org(org_id):
        async with SessionLocal() as session:
            exists = await session.scalar(
                select(Workflow.id).where(Workflow.id == workflow_id)
            )
            if exists is None:
                await websocket.close(code=1008)
                return
    await websocket.accept()
    stop_event = asyncio.Event()
    event_stream = workflow_broker.subscribe(workflow_id)
    event_task: asyncio.Task | None = None
    try:
        org_scope = run_as_org(org_id)
        org_scope.__enter__()

        async def _heartbeat() -> None:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=30)
                    break
                except TimeoutError:
                    pass
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    stop_event.set()
                    break

        async def _watch_disconnect() -> None:
            while not stop_event.is_set():
                try:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        stop_event.set()
                        break
                except WebSocketDisconnect:
                    stop_event.set()
                    break
                except Exception:
                    stop_event.set()
                    break

        hb_task = asyncio.create_task(_heartbeat())
        disconnect_task = asyncio.create_task(_watch_disconnect())
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            event_task = asyncio.create_task(anext(event_stream))
            while not stop_event.is_set():
                done, _pending = await asyncio.wait(
                    {event_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    break
                try:
                    event = event_task.result()
                except StopAsyncIteration:
                    break
                event_task = asyncio.create_task(anext(event_stream))
                try:
                    await websocket.send_json(event)
                except (RuntimeError, WebSocketDisconnect):
                    stop_event.set()
                    break
        finally:
            stop_event.set()
            for task in (event_task, stop_task, hb_task, disconnect_task):
                if task is not None:
                    task.cancel()
            for task in (event_task, stop_task, hb_task, disconnect_task):
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            with contextlib.suppress(Exception):
                await event_stream.aclose()
    finally:
        with contextlib.suppress(Exception):
            org_scope.__exit__(None, None, None)
        with contextlib.suppress(Exception):
            await websocket.close()


@router.get(
    "/{workflow_id}/provider-triggers",
    response_model=list[ProviderTriggerSubscriptionInfo],
)
async def list_provider_triggers(
    workflow_id: str,
    include_deleted: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    _user: User | None = Depends(optional_current_user),
):
    await _load(session, workflow_id)
    query = select(ProviderTriggerSubscription).where(
        ProviderTriggerSubscription.workflow_id == workflow_id
    )
    if not include_deleted:
        query = query.where(ProviderTriggerSubscription.status != "deleted")
    rows = (
        await session.scalars(
            query.order_by(
                ProviderTriggerSubscription.status.asc(),
                ProviderTriggerSubscription.updated_at.desc(),
                ProviderTriggerSubscription.node_id.asc(),
            )
        )
    ).all()
    return list(rows)


@router.get(
    "/{workflow_id}/versions",
    response_model=list[WorkflowVersionInfo],
    dependencies=[Depends(require_permission("workflow:read"))],
)
async def list_versions(workflow_id: str, session: AsyncSession = Depends(get_session)):
    workflow = await _load(session, workflow_id)
    return [
        WorkflowVersionInfo(
            id=v.id,
            version=v.version,
            notes=v.notes,
            created_at=v.created_at,
            node_count=len((v.graph or {}).get("nodes", [])),
            published=(v.version == workflow.published_version),
        )
        for v in workflow.versions
    ]


@router.put(
    "/{workflow_id}",
    response_model=WorkflowDetail,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    workflow = await _load(session, workflow_id)
    if body.name is not None:
        workflow.name = body.name
    if body.active is not None:
        workflow.active = body.active
    sent = body.model_fields_set
    if "environment_id" in sent:
        if body.environment_id is not None and await session.scalar(
            select(Environment).where(Environment.id == body.environment_id)
        ) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found")
        workflow.environment_id = body.environment_id
    if "default_runner_pool_id" in sent:
        if body.default_runner_pool_id is not None and await session.scalar(
            select(RunnerPool).where(RunnerPool.id == body.default_runner_pool_id)
        ) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
        from app.services.isolation import validate_pool_assignment

        await validate_pool_assignment(
            session, workflow.org_id, body.default_runner_pool_id
        )
        workflow.default_runner_pool_id = body.default_runner_pool_id
    if "error_workflow_id" in sent and body.error_workflow_id is not None:
        if body.error_workflow_id == workflow.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "A workflow cannot use itself as its error workflow.",
            )
        # Filtered select (not session.get) so a cross-org error_workflow_id is
        # rejected by the ORM org-filter hook (R-2).
        if await session.scalar(
            select(Workflow).where(Workflow.id == body.error_workflow_id)
        ) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Error workflow not found")
        workflow.error_workflow_id = body.error_workflow_id
    elif "error_workflow_id" in sent:
        workflow.error_workflow_id = None
    if body.error_alerts is not None:
        workflow.error_alerts = body.error_alerts
    if body.allow_concurrent is not None:
        workflow.allow_concurrent = body.allow_concurrent
    if body.execution_mode is not None:
        if body.execution_mode not in VALID_EXECUTION_MODES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"execution_mode must be one of {VALID_EXECUTION_MODES}",
            )
        workflow.execution_mode = body.execution_mode
    if "sandbox_resources" in sent:
        if body.sandbox_resources is None:
            workflow.sandbox_resources = None
        else:
            try:
                workflow.sandbox_resources = validate_sandbox_resources(
                    body.sandbox_resources
                )
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
                ) from exc
    if "requirements" in sent:
        workflow.requirements = _validate_requirement_lines(body.requirements)
    if "run_timeout_seconds" in sent:
        workflow.run_timeout_seconds = body.run_timeout_seconds
    if "artifact_retention_days" in sent:
        workflow.artifact_retention_days = body.artifact_retention_days
    if body.mcp_enabled is not None:
        workflow.mcp_enabled = body.mcp_enabled
    if "mcp_tool_name" in sent:
        name_value = (body.mcp_tool_name or "").strip()
        if name_value and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name_value):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "mcp_tool_name must match [A-Za-z0-9_-]{1,64}.",
            )
        if name_value:
            from app.mcp.tools import STATIC_TOOLS, workflow_tool_name

            if name_value in {tool.name for tool in STATIC_TOOLS}:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "mcp_tool_name is reserved by a built-in Nodyra tool.",
                )
            others = list(
                (
                    await session.scalars(
                        select(Workflow).where(
                            Workflow.id != workflow.id,
                            Workflow.mcp_enabled.is_(True),
                        )
                    )
                ).all()
            )
            if any(workflow_tool_name(other) == name_value for other in others):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "mcp_tool_name is already used in this organization.",
                )
        workflow.mcp_tool_name = name_value or None
    if "mcp_description" in sent:
        workflow.mcp_description = (body.mcp_description or "").strip() or None
    if "mcp_parameters_schema" in sent:
        schema = body.mcp_parameters_schema
        if schema is not None:
            if schema.get("type") not in (None, "object"):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "mcp_parameters_schema must describe an object.",
                )
            schema = {"type": "object", **schema}
            try:
                validator_for(schema).check_schema(schema)
            except SchemaError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Invalid mcp_parameters_schema: {exc.message}",
                ) from exc
        workflow.mcp_parameters_schema = schema
    if body.mcp_enabled is True:
        from app.mcp.tools import STATIC_TOOLS, workflow_tool_name

        effective_name = workflow_tool_name(workflow)
        if effective_name in {tool.name for tool in STATIC_TOOLS}:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "The effective MCP tool name is reserved by Nodyra.",
            )
        others = list(
            (
                await session.scalars(
                    select(Workflow).where(
                        Workflow.id != workflow.id,
                        Workflow.mcp_enabled.is_(True),
                    )
                )
            ).all()
        )
        if any(workflow_tool_name(other) == effective_name for other in others):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "The effective MCP tool name is already used in this organization.",
            )
    if "folder_id" in body.model_fields_set:
        if body.folder_id is not None:
            if await session.scalar(
                select(Folder).where(Folder.id == body.folder_id)
            ) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
        workflow.folder_id = body.folder_id
    if body.graph is not None:
        if (
            body.expected_graph_revision is not None
            and body.expected_graph_revision != workflow.graph_revision
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "message": "Workflow draft changed before this save completed.",
                    "expected_graph_revision": body.expected_graph_revision,
                    "current_graph_revision": workflow.graph_revision,
                },
        )
        _validate_node_types(body.graph)
        workflow.draft_graph = body.graph.model_dump()
        bump_graph_revision(workflow)
        graph_patch = {
            "type": "graph_replaced",
            "node_count": len(workflow.draft_graph.get("nodes", [])),
            "edge_count": len(workflow.draft_graph.get("edges", [])),
        }
        record_workflow_revision(
            session,
            workflow,
            origin="ui",
            operation="set_graph",
            actor=actor,
            patch=graph_patch,
        )
    if body.active is not None:
        await sync_workflow_provider_triggers(
            session,
            workflow,
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
        )
    if body.graph is not None:
        await enqueue_github_push(session, workflow, "ui")
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Workflow conflicts with an existing tenant-scoped value.",
        ) from exc
    if body.graph is not None:
        notify_sync_workers()
        publish_workflow_graph_changed(
            workflow,
            origin="ui",
            operation="set_graph",
            actor=actor,
            node_count=len(workflow.draft_graph.get("nodes", [])),
            edge_count=len(workflow.draft_graph.get("edges", [])),
            patch=graph_patch,
        )
    else:
        publish_workflow_event(
            workflow,
            WORKFLOW_UPDATED,
            origin="ui",
            operation="update",
            actor=actor,
        )
    return await _detail(session, await _load(session, workflow_id))


@router.patch(
    "/{workflow_id}",
    response_model=WorkflowDetail,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def patch_workflow(
    workflow_id: str,
    body: WorkflowUpdate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    """Partial-update alias for PUT — accepts the same body shape but only
    applies fields present in the request. Use this from the editor's
    autosave path so settings tweaks don't have to re-send the full graph."""
    return await update_workflow(workflow_id, body, session, actor)


@router.get(
    "/{workflow_id}/versions/{version_id}",
    response_model=WorkflowVersionInfo,
    dependencies=[Depends(require_permission("workflow:read"))],
)
async def get_version(
    workflow_id: str,
    version_id: str,
    session: AsyncSession = Depends(get_session),
):
    workflow = await _load(session, workflow_id)
    for v in workflow.versions:
        if v.id == version_id:
            return WorkflowVersionInfo(
                id=v.id, version=v.version, notes=v.notes, created_at=v.created_at,
                node_count=len((v.graph or {}).get("nodes", [])),
                published=(v.version == workflow.published_version),
            )
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")


class WorkflowVersionGraph(BaseModel):
    graph: dict


@router.get(
    "/{workflow_id}/versions/{version_id}/graph",
    response_model=WorkflowVersionGraph,
    dependencies=[Depends(require_permission("workflow:read"))],
)
async def get_version_graph(
    workflow_id: str,
    version_id: str,
    session: AsyncSession = Depends(get_session),
):
    workflow = await _load(session, workflow_id)
    for v in workflow.versions:
        if v.id == version_id:
            return WorkflowVersionGraph(graph=v.graph or {"nodes": [], "edges": []})
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")


@router.post(
    "/{workflow_id}/publish",
    response_model=WorkflowPublishResponse,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def publish_workflow(
    workflow_id: str,
    body: WorkflowPublishRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    if body is None:
        body = WorkflowPublishRequest()
    workflow = await _load(session, workflow_id)
    latest = _latest(workflow)
    graph = _draft_graph(workflow)
    if latest is not None and graph == (latest.graph or EMPTY_GRAPH):
        updated_deployments = 0
        if body.update_deployments:
            updated_deployments = await _sync_deployments_to_published_graph(
                session,
                workflow.id,
                latest.id,
                graph,
            )
            await session.commit()
        return WorkflowPublishResponse(
            workflow_id=workflow.id,
            workflow_version_id=latest.id,
            version=latest.version,
            updated_deployments=updated_deployments,
        )

    next_version = latest.version + 1 if latest is not None else 1
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=next_version,
        graph=graph,
        notes=body.notes,
    )
    workflow.versions.append(version)
    workflow.published_version = next_version
    # Publishing a release takes it live: triggers run in production until the
    # author explicitly pauses it (Active toggle / Unpublish). Without this a
    # freshly published workflow stays active=false with no way to go live.
    workflow.active = True
    await session.flush()

    # ── Webhook path collision detection ──────────────────────────────
    # Warn when two active workflows register the same webhook or API
    # endpoint path.  Collisions are *not* an error — both fire on
    # matching requests — but the operator should know so they can
    # disambiguate if needed.
    _colliding_paths: list[str] = []
    _seen_in_graph: set[str] = set()
    for node in graph.get("nodes", []):
        node_type = node.get("type")
        node_params = node.get("params") or {}
        if node_type == "webhook_trigger":
            p = str(node_params.get("path") or "").strip("/")
        elif node_type == "api_endpoint":
            p = str(node_params.get("base_path") or "").strip("/")
        else:
            continue
        if not p or p in _seen_in_graph:
            continue
        _seen_in_graph.add(p)
        # Look for any other *active* workflow whose latest version has
        # a matching trigger path.
        clash = await session.scalar(
            select(Workflow.id)
            .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
            .where(
                Workflow.id != workflow.id,
                Workflow.active.is_(True),
                # Latest version only — see _latest_versions_by_id pattern.
                WorkflowVersion.version == Workflow.published_version,
                WorkflowVersion.graph != None,  # noqa: E711
            )
            .limit(1)
        )
        if clash is not None:
            # Verify the other workflow actually has a matching path.
            clash_wf = await session.scalar(select(Workflow).where(Workflow.id == clash))
            if clash_wf is not None:
                clash_graph = clash_wf.draft_graph or (
                    (await session.scalar(
                        select(WorkflowVersion.graph)
                        .where(
                            WorkflowVersion.workflow_id == clash_wf.id,
                            WorkflowVersion.version == clash_wf.published_version,
                        )
                    )) or {}
                )
                for cn in clash_graph.get("nodes", []):
                    cnp = cn.get("params") or {}
                    if cn.get("type") == "webhook_trigger":
                        cp = str(cnp.get("path") or "").strip("/")
                    elif cn.get("type") == "api_endpoint":
                        cp = str(cnp.get("base_path") or "").strip("/")
                    else:
                        continue
                    if cp == p:
                        _colliding_paths.append(p)
                        break
    if _colliding_paths:
        logger.warning(
            "webhook path collision detected — workflow %s (%s) shares paths %s "
            "with other active workflows. Both will fire on matching requests.",
            workflow.id, workflow.name, _colliding_paths,
        )

    # ── Webhook auth enforcement ────────────────────────────────────────
    # When ``webhook_require_auth`` is on, every webhook_trigger and
    # api_endpoint node MUST configure at least one auth method.  This
    # prevents accidentally exposing an unauthenticated public endpoint.
    if settings.webhook_require_auth:
        _unauthenticated: list[str] = []
        for node in graph.get("nodes", []):
            node_type = node.get("type")
            if node_type not in ("webhook_trigger", "api_endpoint"):
                continue
            node_params = node.get("params") or {}
            auth_type = str(node_params.get("auth_type") or "none")
            if auth_type == "none":
                path = str(node_params.get("path") or node_params.get("base_path") or "").strip("/")
                _unauthenticated.append(
                    f"{node_type} '{node.get('name', node.get('id', 'unnamed'))}' "
                    f"at path '{path or '/'}'"
                )
        if _unauthenticated:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": (
                        "Webhook auth is required (settings.webhook_require_auth=True). "
                        "Configure basic, header, bearer, jwt, or hmac authentication "
                        "on the listed nodes, or set webhook_require_auth=False to "
                        "allow open endpoints."
                    ),
                    "unauthenticated_nodes": _unauthenticated,
                },
            )

    updated_deployments = 0
    if body.update_deployments:
        updated_deployments = await _sync_deployments_to_published_graph(
            session,
            workflow.id,
            version.id,
            graph,
        )

    await log_audit(
        session,
        "publish",
        "workflow",
        workflow.id,
        f"v{next_version}: {workflow.name}",
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    if workflow.active:
        await sync_workflow_provider_triggers(
            session,
            workflow,
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
        )
    await enqueue_github_push(session, workflow, "publish")
    await session.commit()
    notify_sync_workers()
    publish_workflow_event(
        workflow,
        WORKFLOW_PUBLISHED,
        origin="ui",
        operation="publish",
        actor=actor,
        workflow_version_id=version.id,
        version=next_version,
        updated_deployments=updated_deployments,
    )
    return WorkflowPublishResponse(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        version=next_version,
        updated_deployments=updated_deployments,
    )


@router.post(
    "/{workflow_id}/ai-draft",
    response_model=AiWorkflowDraftResponse,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def create_ai_workflow_draft(
    workflow_id: str,
    body: AiWorkflowDraftRequest,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    workflow = await _load(session, workflow_id)
    draft = await build_workflow_draft(session, workflow_id, body)
    if body.apply:
        workflow.draft_graph = draft.graph.model_dump()
        bump_graph_revision(workflow)
        graph_patch = {
            "type": "graph_replaced",
            "node_count": len(workflow.draft_graph.get("nodes", [])),
            "edge_count": len(workflow.draft_graph.get("edges", [])),
        }
        record_workflow_revision(
            session,
            workflow,
            origin="ai",
            operation="ai_draft",
            actor=actor,
            patch=graph_patch,
            summary="AI workflow draft applied",
        )
        await log_audit(
            session,
            "ai_draft",
            "workflow",
            workflow.id,
            "AI workflow draft applied",
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
        )
        await session.commit()
        publish_workflow_graph_changed(
            workflow,
            origin="ai",
            operation="ai_draft",
            node_count=len(workflow.draft_graph.get("nodes", [])),
            edge_count=len(workflow.draft_graph.get("edges", [])),
            patch=graph_patch,
        )
    return draft


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def delete_workflow(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    workflow = await _load(session, workflow_id)
    await log_audit(session, "delete", "workflow", workflow_id, workflow.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.delete(workflow)
    await session.commit()
    publish_workflow_event(
        workflow,
        WORKFLOW_DELETED,
        origin="ui",
        operation="delete",
        actor=actor,
    )


@router.post(
    "/{workflow_id}/nodes/{node_id}/test",
    response_model=NodeTestResponse,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def test_workflow_node(
    workflow_id: str,
    node_id: str,
    body: NodeTestRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> NodeTestResponse:
    """Run one workflow node against supplied/pinned upstream outputs.

    This is intentionally ephemeral: no Run, NodeRun, RunEvent, or Artifact DB
    rows are created. Upstream nodes may appear in the engine plan only as
    cached values, so their functions are not invoked.
    """

    body = body or NodeTestRequest()
    workflow = await _load(session, workflow_id)
    graph_dict = _select_graph(workflow, use_draft=body.use_draft)
    graph = WorkflowGraph.model_validate(graph_dict)
    if not _node_exists(graph, node_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")

    cache: dict[str, dict[str, Any]] = {}
    if body.use_pinned:
        pinned_rows = await session.scalars(
            select(PinnedData).where(PinnedData.workflow_id == workflow_id)
        )
        cache.update({row.node_id: dict(row.payload) for row in pinned_rows.all()})
    if body.cache:
        cache.update({key: dict(value) for key, value in body.cache.items()})
    # Pins/cache seed upstream values only. The selected node must execute even
    # when its previous output is pinned or supplied in an over-broad cache.
    cache.pop(node_id, None)
    cache = _overlay_direct_inputs(graph, node_id, cache, body.inputs)

    missing = _missing_direct_upstream(graph, node_id, cache)
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Missing cached upstream output(s) for single-node test: "
            + ", ".join(missing),
        )

    ephemeral_run_id = f"node-test-{uuid4().hex}"
    prep = await runner_service._prepare_run_context(
        ephemeral_run_id,
        workflow_id,
        graph_dict,
        cache or None,
        workflow.org_id,
    )

    loaded_module_ids: list[str] = []
    artifact_token = artifact_store.set(
        make_artifact_store(
            ephemeral_run_id,
            org_id=workflow.org_id,
            max_bytes=prep.max_artifact_bytes,
            max_count=prep.max_artifacts_per_run,
        )
    )
    limits_token = org_run_limits.set(await _org_run_limits_for(workflow.org_id))
    try:
        for module in prep.workflow_modules:
            if not module.get("contents", "").strip():
                continue
            register_module_functions(
                module["id"],
                module["contents"],
                node_registry,
                include_undecorated=bool(module.get("include_undecorated")),
            )
            loaded_module_ids.append(module["id"])

        prepared_graph = WorkflowGraph.model_validate(prep.graph_dict)
        async with runtime_pool.global_slot():
            result = await execute(
                prepared_graph,
                node_registry,
                cache=deserialize_value(prep.cache or {}),
                targets=[node_id],
                default_timeouts=runner_service._engine_default_timeouts(),
                max_node_output_bytes=prep.output_cap,
                process_isolator=runner_service.process_isolator,
            )
    finally:
        org_run_limits.reset(limits_token)
        artifact_store.reset(artifact_token)
        for module_id in loaded_module_ids:
            unregister_module(module_id, node_registry)

    node_result = result.nodes.get(node_id)
    if node_result is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Node test did not produce a target result",
        )

    duration_ms: int | None = None
    if node_result.started_at is not None and node_result.finished_at is not None:
        duration_ms = int((node_result.finished_at - node_result.started_at) * 1000)

    return NodeTestResponse(
        workflow_id=workflow_id,
        node_id=node_id,
        status=str(node_result.status),
        output=serialize_value(node_result.outputs),
        error=node_result.error,
        logs=serialize_value(node_result.logs),
        debug=serialize_value(node_result.debug),
        started_at=node_result.started_at,
        finished_at=node_result.finished_at,
        duration_ms=duration_ms,
        cached_node_ids=sorted(cache),
    )


@router.post(
    "/{workflow_id}/explain",
    dependencies=[Depends(require_permission("workflow:read"))],
)
async def explain_workflow(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a natural-language explanation of the workflow graph."""
    from app.services.ai_builder import explain_workflow as _explain

    wf = await session.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if wf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    graph = wf.draft_graph or {}
    return await _explain(graph)


@router.post(
    "/{workflow_id}/generate-tests",
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def generate_workflow_tests(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate test cases for a workflow using AI."""
    from app.services.ai_builder import generate_tests as _gen_tests

    wf = await session.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if wf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    graph = wf.draft_graph or {}
    tests = await _gen_tests(graph)
    # Persist tests to the workflow
    wf.tests = tests
    await session.commit()
    return {"tests": tests}


@router.get(
    "/{workflow_id}/checks",
    response_model=list[WorkflowCheckInfo],
    dependencies=[Depends(require_permission("workflow:read"))],
)
async def list_workflow_checks(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowCheckInfo]:
    await _load(session, workflow_id)
    checks = (
        await session.scalars(
            select(WorkflowCheck)
            .where(WorkflowCheck.workflow_id == workflow_id)
            .order_by(WorkflowCheck.created_at.asc())
        )
    ).all()
    return [_check_info(check) for check in checks]


@router.post(
    "/{workflow_id}/checks",
    response_model=list[WorkflowCheckInfo],
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def save_workflow_checks(
    workflow_id: str,
    body: WorkflowChecksSaveRequest,
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowCheckInfo]:
    workflow = await _load(session, workflow_id)
    cases = [case.model_dump(mode="json") for case in body.checks]
    if body.replace:
        await session.execute(
            delete(WorkflowCheck).where(WorkflowCheck.workflow_id == workflow_id)
        )

    checks: list[WorkflowCheck] = []
    for case in cases:
        check = WorkflowCheck(
            org_id=workflow.org_id,
            workflow_id=workflow.id,
            name=case["name"],
            input_data=case.get("input_data") or {},
            expected_outputs=case.get("expected_outputs") or {},
            assertions=case.get("assertions") or [],
        )
        session.add(check)
        checks.append(check)

    workflow.tests = cases
    await session.flush()
    await session.commit()
    return [_check_info(check) for check in checks]


@router.post(
    "/{workflow_id}/checks/run",
    response_model=list[WorkflowCheckRunResult],
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def run_workflow_checks(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowCheckRunResult]:
    workflow = await _load(session, workflow_id)
    checks = (
        await session.scalars(
            select(WorkflowCheck)
            .where(WorkflowCheck.workflow_id == workflow_id)
            .order_by(WorkflowCheck.created_at.asc())
        )
    ).all()
    results = [
        await _run_and_persist_check(session, workflow, check) for check in checks
    ]
    await session.commit()
    return results


@router.post(
    "/{workflow_id}/checks/{check_id}/run",
    response_model=WorkflowCheckRunResult,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def run_workflow_check(
    workflow_id: str,
    check_id: str,
    session: AsyncSession = Depends(get_session),
) -> WorkflowCheckRunResult:
    workflow = await _load(session, workflow_id)
    check = await session.scalar(
        select(WorkflowCheck).where(
            WorkflowCheck.workflow_id == workflow_id,
            WorkflowCheck.id == check_id,
        )
    )
    if check is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow check not found")
    result = await _run_and_persist_check(session, workflow, check)
    await session.commit()
    return result


@router.delete(
    "/{workflow_id}/checks/{check_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def delete_workflow_check(
    workflow_id: str,
    check_id: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _load(session, workflow_id)
    result = await session.execute(
        delete(WorkflowCheck).where(
            WorkflowCheck.workflow_id == workflow_id,
            WorkflowCheck.id == check_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow check not found")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
