"""Deployments: schedulable, parametrized instances of a workflow.

A deployment couples a workflow to a schedule + default parameters + on-off
toggle, and lives as its own DB row (rather than as state on the graph).
When a workflow has any active deployments, the scheduler iterates those
instead of the in-graph ``schedule_trigger`` so it can't be fired twice.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Deployment, Run, Workflow
from app.schemas import (
    DeploymentCreate,
    DeploymentInfo,
    DeploymentUpdate,
    RunCreated,
    RunListItem,
)
from app.services.audit import log_audit
from app.services.runner import start_run

router = APIRouter(prefix="/deployments", tags=["deployments"])


async def _load(session: AsyncSession, deployment_id: str) -> Deployment:
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found")
    return deployment


@router.get("", response_model=list[DeploymentInfo])
async def list_deployments(
    workflow_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Deployment).order_by(Deployment.created_at.desc())
    if workflow_id is not None:
        stmt = stmt.where(Deployment.workflow_id == workflow_id)
    result = await session.scalars(stmt)
    return list(result.all())


@router.post("", response_model=DeploymentInfo, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    body: DeploymentCreate, session: AsyncSession = Depends(get_session)
):
    workflow = await session.get(Workflow, body.workflow_id)
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")

    deployment = Deployment(
        workflow_id=body.workflow_id,
        name=body.name,
        schedule_cron=body.schedule_cron,
        schedule_interval=body.schedule_interval,
        schedule_every=max(1, body.schedule_every),
        schedule_tz=body.schedule_tz,
        default_parameters=body.default_parameters,
        active=body.active,
        environment_id=body.environment_id,
    )
    session.add(deployment)
    await log_audit(session, "create", "deployment", detail=body.name)
    await session.commit()
    await session.refresh(deployment)
    return deployment


@router.get("/{deployment_id}", response_model=DeploymentInfo)
async def get_deployment(
    deployment_id: str, session: AsyncSession = Depends(get_session)
):
    return await _load(session, deployment_id)


@router.put("/{deployment_id}", response_model=DeploymentInfo)
async def update_deployment(
    deployment_id: str,
    body: DeploymentUpdate,
    session: AsyncSession = Depends(get_session),
):
    deployment = await _load(session, deployment_id)
    if body.name is not None:
        deployment.name = body.name
    if body.schedule_cron is not None:
        deployment.schedule_cron = body.schedule_cron
    if body.schedule_interval is not None:
        deployment.schedule_interval = body.schedule_interval
    if body.schedule_every is not None:
        deployment.schedule_every = max(1, body.schedule_every)
    if body.schedule_tz is not None:
        deployment.schedule_tz = body.schedule_tz
    if body.default_parameters is not None:
        deployment.default_parameters = body.default_parameters
    if body.active is not None:
        deployment.active = body.active
    if body.environment_id is not None:
        deployment.environment_id = body.environment_id
    await session.commit()
    await session.refresh(deployment)
    return deployment


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(
    deployment_id: str, session: AsyncSession = Depends(get_session)
):
    deployment = await _load(session, deployment_id)
    await log_audit(
        session, "delete", "deployment", deployment.id, deployment.name
    )
    await session.delete(deployment)
    await session.commit()


@router.post("/{deployment_id}/run", response_model=RunCreated)
async def run_deployment(
    deployment_id: str, session: AsyncSession = Depends(get_session)
):
    """'Run now' — fire the deployment with its default parameters."""
    deployment = await _load(session, deployment_id)
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == deployment.workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    latest = workflow.versions[-1]
    run_id = await start_run(
        deployment.workflow_id,
        latest.graph or {"nodes": [], "edges": []},
        latest.version,
        mode="manual",
        trigger_type="deployment",
        parameters=deployment.default_parameters or None,
    )
    return RunCreated(run_id=run_id)


@router.get("/{deployment_id}/runs", response_model=list[RunListItem])
async def list_deployment_runs(
    deployment_id: str,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """Recent runs for this deployment's workflow.

    v1 keys off ``workflow_id`` (the Run schema has no direct deployment_id
    yet). If a workflow has multiple deployments, all of them share the
    same run history.
    """
    deployment = await _load(session, deployment_id)
    stmt = (
        select(Run, Workflow.name)
        .join(Workflow, Run.workflow_id == Workflow.id)
        .where(Run.workflow_id == deployment.workflow_id)
        .order_by(Run.started_at.desc())
        .limit(max(1, min(200, limit)))
    )
    rows = (await session.execute(stmt)).all()
    return [
        RunListItem(
            id=run.id,
            workflow_id=run.workflow_id,
            workflow_name=workflow_name,
            workflow_version=run.workflow_version,
            mode=run.mode,
            status=run.status,
            trigger_type=run.trigger_type,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
        for run, workflow_name in rows
    ]
