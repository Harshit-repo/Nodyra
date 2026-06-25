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

from app.config import settings
from app.db import get_session
from app.models import Deployment, Run, User, Workflow, WorkflowVersion
from app.schemas import (
    DeploymentCreate,
    DeploymentInfo,
    DeploymentUpdate,
    RunCreated,
    RunListItem,
)
from app.security import optional_current_user, require_permission
from app.services.audit import log_audit
from app.services.graph_utils import first_trigger_node
from app.services.runner import start_run
from app.services.unsafe_nodes import classify as classify_unsafe_nodes

router = APIRouter(prefix="/deployments", tags=["deployments"])


def _enforce_unsafe_node_policy(
    version: WorkflowVersion, *, approved: bool
) -> None:
    """Raise 409 (with the findings) when the configured policy blocks activation.

    Findings travel back in the response detail so the UI can render the
    same list the operator needs to acknowledge. Callers must already know
    the deployment is becoming ``active=True`` — this helper assumes that.
    """
    policy = settings.unsafe_node_policy
    if policy == "allow":
        return
    findings = classify_unsafe_nodes(version.graph or {})
    if not findings:
        return
    if policy == "warn":
        return
    if policy == "require_approval" and approved:
        return
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "message": (
                "Workflow contains risky nodes that the unsafe-node policy "
                f"({policy!r}) does not allow."
            ),
            "policy": policy,
            "findings": findings,
        },
    )


def _validate_deployable(version: WorkflowVersion) -> None:
    """Reject deploying a version whose graph can't run (empty / no trigger).

    Without this the only symptom is a 400 at every fire — a scheduled
    deployment would fail silently on its interval forever. Checked at
    create/activate/run-now so the operator finds out immediately.
    """
    graph = version.graph or {}
    nodes = graph.get("nodes") or []
    if not nodes or first_trigger_node(graph) is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This workflow version has no trigger node, so the deployment "
            "can't run. Add a trigger and republish before deploying.",
        )


async def _load(session: AsyncSession, deployment_id: str) -> Deployment:
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found")
    return deployment


def _latest_published(workflow: Workflow) -> WorkflowVersion:
    return workflow.versions[-1]


async def _version_for_deployment(
    session: AsyncSession,
    workflow: Workflow,
    workflow_version_id: str | None,
) -> WorkflowVersion:
    if workflow_version_id is None:
        return _latest_published(workflow)
    version = await session.get(WorkflowVersion, workflow_version_id)
    if version is None or version.workflow_id != workflow.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "workflow_version_id must belong to the deployment workflow.",
        )
    return version


async def _info(
    session: AsyncSession,
    deployment: Deployment,
    *,
    versions: dict[str, WorkflowVersion] | None = None,
) -> DeploymentInfo:
    workflow_version = None
    if deployment.workflow_version_id:
        if versions is not None:
            version = versions.get(deployment.workflow_version_id)
        else:
            version = await session.get(WorkflowVersion, deployment.workflow_version_id)
        workflow_version = version.version if version is not None else None
    return DeploymentInfo(
        id=deployment.id,
        workflow_id=deployment.workflow_id,
        name=deployment.name,
        schedule_cron=deployment.schedule_cron,
        schedule_interval=deployment.schedule_interval,
        schedule_every=deployment.schedule_every,
        schedule_tz=deployment.schedule_tz,
        default_parameters=deployment.default_parameters,
        active=deployment.active,
        environment_id=deployment.environment_id,
        runner_pool_id=deployment.runner_pool_id,
        workflow_version_id=deployment.workflow_version_id,
        workflow_version=workflow_version,
        error_workflow_id=deployment.error_workflow_id,
        error_alerts=deployment.error_alerts or {},
        last_fired=deployment.last_fired,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
    )


@router.get("", response_model=list[DeploymentInfo])
async def list_deployments(
    workflow_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Deployment).order_by(Deployment.created_at.desc())
    if workflow_id is not None:
        stmt = stmt.where(Deployment.workflow_id == workflow_id)
    result = await session.scalars(stmt)
    deployments = result.all()
    version_ids = {
        d.workflow_version_id for d in deployments if d.workflow_version_id
    }
    versions: dict[str, WorkflowVersion] = {}
    if version_ids:
        versions = {
            v.id: v
            for v in (
                await session.scalars(
                    select(WorkflowVersion).where(WorkflowVersion.id.in_(version_ids))
                )
            ).all()
        }
    return [
        await _info(session, deployment, versions=versions)
        for deployment in deployments
    ]


@router.post(
    "",
    response_model=DeploymentInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("deployment:write"))],
)
async def create_deployment(
    body: DeploymentCreate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == body.workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    version = await _version_for_deployment(session, workflow, body.workflow_version_id)
    if body.error_workflow_id is not None and await session.scalar(
        select(Workflow).where(Workflow.id == body.error_workflow_id)
    ) is None:
        # Filtered select (not session.get) so the ORM org-filter hook fires:
        # a cross-org error_workflow_id resolves to None here and is rejected.
        # Otherwise org-A could point its error handler at org-B's workflow and
        # (with the run_as_system error dispatch) leak its failure payload (R-2).
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Error workflow not found")

    if body.active:
        _validate_deployable(version)
        _enforce_unsafe_node_policy(version, approved=body.approve_unsafe_nodes)
        from app.services.licensing import enforce_resource_cap
        await enforce_resource_cap(session, "deployments")

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
        runner_pool_id=body.runner_pool_id,
        workflow_version_id=version.id,
        error_workflow_id=body.error_workflow_id,
        error_alerts=body.error_alerts,
    )
    session.add(deployment)
    await log_audit(session, "create", "deployment", detail=body.name,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(deployment)
    return await _info(session, deployment)


@router.get("/{deployment_id}", response_model=DeploymentInfo)
async def get_deployment(
    deployment_id: str, session: AsyncSession = Depends(get_session)
):
    return await _info(session, await _load(session, deployment_id))


@router.put(
    "/{deployment_id}",
    response_model=DeploymentInfo,
    dependencies=[Depends(require_permission("deployment:write"))],
)
async def update_deployment(
    deployment_id: str,
    body: DeploymentUpdate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
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
    if body.environment_id is not None:
        deployment.environment_id = body.environment_id
    if body.runner_pool_id is not None:
        deployment.runner_pool_id = body.runner_pool_id
    version_changed = False
    if body.workflow_version_id is not None:
        workflow = await session.scalar(
            select(Workflow)
            .where(Workflow.id == deployment.workflow_id)
            .options(selectinload(Workflow.versions))
        )
        if workflow is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
        version = await _version_for_deployment(
            session, workflow, body.workflow_version_id
        )
        version_changed = deployment.workflow_version_id != version.id
        deployment.workflow_version_id = version.id
    becoming_active = bool(body.active) and not deployment.active
    if becoming_active:
        from app.services.licensing import enforce_resource_cap
        await enforce_resource_cap(session, "deployments")
    if body.active is not None:
        deployment.active = body.active

    # DEP-1: re-evaluate the unsafe-node policy whenever an ACTIVE deployment's
    # effective version could start running risky nodes — i.e. on
    # inactive → active OR when the running version is repointed while already
    # active. Without the latter, an operator could bypass the activation gate by
    # changing an active deployment's ``workflow_version_id`` to a risky version.
    if deployment.active and (becoming_active or version_changed):
        workflow = await session.scalar(
            select(Workflow)
            .where(Workflow.id == deployment.workflow_id)
            .options(selectinload(Workflow.versions))
        )
        if workflow is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
        version = await _version_for_deployment(
            session, workflow, deployment.workflow_version_id
        )
        _validate_deployable(version)
        _enforce_unsafe_node_policy(version, approved=body.approve_unsafe_nodes)
    if body.error_workflow_id is not None:
        # Filtered select (not session.get) so a cross-org error_workflow_id is
        # rejected by the ORM org-filter hook (R-2).
        if await session.scalar(
            select(Workflow).where(Workflow.id == body.error_workflow_id)
        ) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Error workflow not found")
        deployment.error_workflow_id = body.error_workflow_id
    if body.error_alerts is not None:
        deployment.error_alerts = body.error_alerts
    await log_audit(
        session,
        "update",
        "deployment",
        deployment.id,
        deployment.name,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.commit()
    await session.refresh(deployment)
    return await _info(session, deployment)


@router.delete(
    "/{deployment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("deployment:write"))],
)
async def delete_deployment(
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    deployment = await _load(session, deployment_id)
    await log_audit(
        session, "delete", "deployment", deployment.id, deployment.name,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.delete(deployment)
    await session.commit()


@router.post(
    "/{deployment_id}/run",
    response_model=RunCreated,
    dependencies=[Depends(require_permission("deployment:run"))],
)
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
    version = await _version_for_deployment(
        session, workflow, deployment.workflow_version_id
    )
    _validate_deployable(version)
    graph = version.graph or {"nodes": [], "edges": []}
    chosen = first_trigger_node(graph)
    trigger_id = (
        chosen["id"] if isinstance(chosen, dict) else getattr(chosen, "id", None)
    )
    try:
        run_id = await start_run(
            deployment.workflow_id,
            graph,
            version.version,
            workflow_version_id=version.id,
            deployment_id=deployment.id,
            mode="manual",
            trigger_type="deployment",
            parameters=deployment.default_parameters or None,
            trigger_node_id=trigger_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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
            workflow_version_id=run.workflow_version_id,
            deployment_id=run.deployment_id,
            triggered_by_error_run_id=run.triggered_by_error_run_id,
            parent_run_id=run.parent_run_id,
            mode=run.mode,
            status=run.status,
            trigger_type=run.trigger_type,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
        for run, workflow_name in rows
    ]
