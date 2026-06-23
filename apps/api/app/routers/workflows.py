import re

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

import noodle_nodes  # noqa: F401 - registers built-in nodes
from app.db import get_session
from app.models import (
    Environment,
    Folder,
    ProviderTriggerSubscription,
    Run,
    RunnerPool,
    User,
    Workflow,
    WorkflowVersion,
)
from app.schemas import (
    AiWorkflowDraftRequest,
    AiWorkflowDraftResponse,
    PageResponse,
    ProviderTriggerStatusCounts,
    ProviderTriggerSubscriptionInfo,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowPublishRequest,
    WorkflowPublishResponse,
    WorkflowSummary,
    WorkflowUpdate,
    WorkflowVersionInfo,
)
from app.security import optional_current_user, require_permission
from app.services.ai_builder import build_workflow_draft
from app.services.audit import log_audit
from app.services.github_sync import enqueue_github_push
from app.services.github_sync_jobs import notify_sync_workers
from app.services.provider_triggers import sync_workflow_provider_triggers
from noodle.models import WorkflowGraph
from noodle.sdk import registry as node_registry

router = APIRouter(prefix="/workflows", tags=["workflows"])

EMPTY_GRAPH: dict = {"nodes": [], "edges": []}

# Structural node types the engine resolves directly (inlined/expanded at plan
# time) rather than dispatching through a registry manifest. They never appear
# in ``node_registry.manifests()`` but are valid in a persisted graph.
STRUCTURAL_NODE_TYPES: frozenset[str] = frozenset({"meta_node"})


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
            status.HTTP_422_UNPROCESSABLE_ENTITY,
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


def _latest(workflow: Workflow) -> WorkflowVersion:
    return workflow.versions[-1]


def _draft_graph(workflow: Workflow) -> dict:
    if workflow.draft_graph is not None:
        return workflow.draft_graph
    return _latest(workflow).graph or EMPTY_GRAPH


def _has_unpublished_changes(workflow: Workflow) -> bool:
    return _draft_graph(workflow) != (_latest(workflow).graph or EMPTY_GRAPH)


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
        has_unpublished_changes=_has_unpublished_changes(workflow),
        environment_id=workflow.environment_id,
        default_runner_pool_id=workflow.default_runner_pool_id,
        folder_id=workflow.folder_id,
        error_workflow_id=workflow.error_workflow_id,
        error_alerts=workflow.error_alerts or {},
        allow_concurrent=workflow.allow_concurrent,
        run_timeout_seconds=workflow.run_timeout_seconds,
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
    )


@router.get("", response_model=PageResponse[WorkflowSummary])
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
        .options(selectinload(Workflow.versions))
        .order_by(Workflow.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    workflows = result.all()
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
    await session.commit()
    notify_sync_workers()
    return await _detail(session, await _load(session, workflow.id))


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(workflow_id: str, session: AsyncSession = Depends(get_session)):
    return await _detail(session, await _load(session, workflow_id))


@router.get(
    "/{workflow_id}/provider-triggers",
    response_model=list[ProviderTriggerSubscriptionInfo],
)
async def list_provider_triggers(
    workflow_id: str,
    include_deleted: bool = Query(False),
    session: AsyncSession = Depends(get_session),
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


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionInfo])
async def list_versions(workflow_id: str, session: AsyncSession = Depends(get_session)):
    workflow = await _load(session, workflow_id)
    return [
        WorkflowVersionInfo(
            id=v.id,
            version=v.version,
            notes=v.notes,
            created_at=v.created_at,
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
    if "run_timeout_seconds" in sent:
        workflow.run_timeout_seconds = body.run_timeout_seconds
    if body.mcp_enabled is not None:
        workflow.mcp_enabled = body.mcp_enabled
    if "mcp_tool_name" in sent:
        name_value = (body.mcp_tool_name or "").strip()
        if name_value and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name_value):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "mcp_tool_name must match [A-Za-z0-9_-]{1,64}.",
            )
        workflow.mcp_tool_name = name_value or None
    if "mcp_description" in sent:
        workflow.mcp_description = (body.mcp_description or "").strip() or None
    if "mcp_parameters_schema" in sent:
        workflow.mcp_parameters_schema = body.mcp_parameters_schema
    if "folder_id" in body.model_fields_set:
        if body.folder_id is not None:
            if await session.scalar(
                select(Folder).where(Folder.id == body.folder_id)
            ) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
        workflow.folder_id = body.folder_id
    if body.graph is not None:
        _validate_node_types(body.graph)
        workflow.draft_graph = body.graph.model_dump()
    if body.active is not None:
        await sync_workflow_provider_triggers(
            session,
            workflow,
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
        )
    if body.graph is not None:
        await enqueue_github_push(session, workflow, "ui")
    await session.commit()
    if body.graph is not None:
        notify_sync_workers()
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
            )
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
    if graph == (latest.graph or EMPTY_GRAPH):
        return WorkflowPublishResponse(
            workflow_id=workflow.id,
            workflow_version_id=latest.id,
            version=latest.version,
            updated_deployments=0,
        )

    next_version = latest.version + 1
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

    updated_deployments = 0
    if body.update_deployments:
        from app.models import Deployment

        deployments = (
            await session.scalars(select(Deployment).where(Deployment.workflow_id == workflow.id))
        ).all()
        for deployment in deployments:
            deployment.workflow_version_id = version.id
            updated_deployments += 1

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
):
    workflow = await _load(session, workflow_id)
    draft = await build_workflow_draft(session, workflow_id, body)
    if body.apply:
        workflow.draft_graph = draft.graph.model_dump()
        await log_audit(
            session,
            "ai_draft",
            "workflow",
            workflow.id,
            "AI workflow draft applied",
        )
        await session.commit()
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
