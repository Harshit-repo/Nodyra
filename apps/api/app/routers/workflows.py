from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Environment, Workflow, WorkflowVersion
from app.schemas import (
    WorkflowCreate,
    WorkflowDetail,
    WorkflowSummary,
    WorkflowUpdate,
    WorkflowVersionInfo,
)
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


async def _global_env_id(session: AsyncSession) -> str | None:
    result = await session.scalars(
        select(Environment).where(Environment.is_global.is_(True)).limit(1)
    )
    env = result.first()
    return env.id if env is not None else None


def _summary(workflow: Workflow) -> WorkflowSummary:
    latest = _latest(workflow)
    graph = latest.graph or EMPTY_GRAPH
    return WorkflowSummary(
        id=workflow.id,
        name=workflow.name,
        active=workflow.active,
        version=latest.version,
        node_count=len(graph.get("nodes", [])),
        environment_id=workflow.environment_id,
        updated_at=workflow.updated_at,
    )


def _detail(workflow: Workflow) -> WorkflowDetail:
    latest = _latest(workflow)
    return WorkflowDetail(
        id=workflow.id,
        name=workflow.name,
        active=workflow.active,
        version=latest.version,
        environment_id=workflow.environment_id,
        graph=WorkflowGraph.model_validate(latest.graph or EMPTY_GRAPH),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(
        select(Workflow).options(selectinload(Workflow.versions))
    )
    return [_summary(w) for w in result.all()]


@router.post("", response_model=WorkflowDetail, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowCreate, session: AsyncSession = Depends(get_session)
):
    workflow = Workflow(name=body.name, environment_id=await _global_env_id(session))
    workflow.versions.append(WorkflowVersion(version=1, graph=dict(EMPTY_GRAPH)))
    session.add(workflow)
    await log_audit(session, "create", "workflow", detail=body.name)
    await session.commit()
    return _detail(await _load(session, workflow.id))


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(workflow_id: str, session: AsyncSession = Depends(get_session)):
    return _detail(await _load(session, workflow_id))


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionInfo])
async def list_versions(
    workflow_id: str, session: AsyncSession = Depends(get_session)
):
    workflow = await _load(session, workflow_id)
    return [
        WorkflowVersionInfo(version=v.version, created_at=v.created_at)
        for v in workflow.versions
    ]


@router.put("/{workflow_id}", response_model=WorkflowDetail)
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
    if body.graph is not None:
        next_version = _latest(workflow).version + 1
        workflow.versions.append(
            WorkflowVersion(version=next_version, graph=body.graph.model_dump())
        )
    await session.commit()
    return _detail(await _load(session, workflow_id))


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str, session: AsyncSession = Depends(get_session)
):
    workflow = await _load(session, workflow_id)
    await log_audit(session, "delete", "workflow", workflow_id, workflow.name)
    await session.delete(workflow)
    await session.commit()
