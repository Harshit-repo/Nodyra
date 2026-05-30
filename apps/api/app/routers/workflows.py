from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Environment, Run, Workflow, WorkflowVersion
from app.schemas import (
    AiWorkflowDraftRequest,
    AiWorkflowDraftResponse,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowPublishRequest,
    WorkflowPublishResponse,
    WorkflowSummary,
    WorkflowUpdate,
    WorkflowVersionInfo,
)
from app.security import require_permission
from app.services.ai_builder import build_workflow_draft
from app.services.audit import log_audit
from noodle.models import WorkflowGraph

router = APIRouter(prefix="/workflows", tags=["workflows"])

EMPTY_GRAPH: dict = {"nodes": [], "edges": []}


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


async def _summary(session: AsyncSession, workflow: Workflow) -> WorkflowSummary:
    graph = _draft_graph(workflow)
    latest_run = await _latest_run(session, workflow.id)
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
        updated_at=workflow.updated_at,
    )


def _detail(workflow: Workflow) -> WorkflowDetail:
    return WorkflowDetail(
        id=workflow.id,
        name=workflow.name,
        active=workflow.active,
        version=workflow.published_version,
        published_version=workflow.published_version,
        has_unpublished_changes=_has_unpublished_changes(workflow),
        environment_id=workflow.environment_id,
        error_workflow_id=workflow.error_workflow_id,
        error_alerts=workflow.error_alerts or {},
        graph=WorkflowGraph.model_validate(_draft_graph(workflow)),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(select(Workflow).options(selectinload(Workflow.versions)))
    return [await _summary(session, w) for w in result.all()]


@router.post(
    "",
    response_model=WorkflowDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def create_workflow(body: WorkflowCreate, session: AsyncSession = Depends(get_session)):
    workflow = Workflow(
        name=body.name,
        environment_id=await _global_env_id(session),
        draft_graph=dict(EMPTY_GRAPH),
        published_version=1,
    )
    workflow.versions.append(WorkflowVersion(version=1, graph=dict(EMPTY_GRAPH)))
    session.add(workflow)
    await log_audit(session, "create", "workflow", detail=body.name)
    await session.commit()
    return _detail(await _load(session, workflow.id))


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(workflow_id: str, session: AsyncSession = Depends(get_session)):
    return _detail(await _load(session, workflow_id))


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
):
    workflow = await _load(session, workflow_id)
    if body.name is not None:
        workflow.name = body.name
    if body.active is not None:
        workflow.active = body.active
    if body.environment_id is not None:
        workflow.environment_id = body.environment_id
    if body.error_workflow_id is not None:
        if body.error_workflow_id == workflow.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "A workflow cannot use itself as its error workflow.",
            )
        if await session.get(Workflow, body.error_workflow_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Error workflow not found")
        workflow.error_workflow_id = body.error_workflow_id
    if body.error_alerts is not None:
        workflow.error_alerts = body.error_alerts
    if body.graph is not None:
        workflow.draft_graph = body.graph.model_dump()
    await session.commit()
    return _detail(await _load(session, workflow_id))


@router.post(
    "/{workflow_id}/publish",
    response_model=WorkflowPublishResponse,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def publish_workflow(
    workflow_id: str,
    body: WorkflowPublishRequest,
    session: AsyncSession = Depends(get_session),
):
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
    )
    await session.commit()
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
async def delete_workflow(workflow_id: str, session: AsyncSession = Depends(get_session)):
    workflow = await _load(session, workflow_id)
    await log_audit(session, "delete", "workflow", workflow_id, workflow.name)
    await session.delete(workflow)
    await session.commit()
