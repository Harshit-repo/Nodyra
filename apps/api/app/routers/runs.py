from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_session
from app.models import PinnedData, Run, Workflow, WorkflowVersion
from app.schemas import (
    RunCancelResponse,
    RunCreated,
    RunInfo,
    RunListItem,
    RunRequest,
)
from app.security import require_permission
from app.services.crypto import verify_token
from app.services.events import broker
from app.services.runner import cancel_run, start_run

router = APIRouter(tags=["runs"])


EMPTY_GRAPH = {"nodes": [], "edges": []}


def _draft_graph(workflow: Workflow) -> dict:
    if workflow.draft_graph is not None:
        return workflow.draft_graph
    return workflow.versions[-1].graph or EMPTY_GRAPH


async def _graph_for_run(
    session: AsyncSession, run: Run, workflow: Workflow
) -> tuple[dict, int, str | None]:
    if run.workflow_version_id:
        version = await session.get(WorkflowVersion, run.workflow_version_id)
        if version is not None:
            return version.graph or EMPTY_GRAPH, version.version, version.id
    latest = workflow.versions[-1]
    return _draft_graph(workflow), latest.version, None


@router.post(
    "/workflows/{workflow_id}/run",
    response_model=RunCreated,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def run_workflow(
    workflow_id: str,
    body: RunRequest,
    session: AsyncSession = Depends(get_session),
):
    workflow = await session.get(
        Workflow, workflow_id, options=[selectinload(Workflow.versions)]
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")

    latest = workflow.versions[-1]
    graph = _draft_graph(workflow)
    pinned_rows = await session.scalars(
        select(PinnedData).where(PinnedData.workflow_id == workflow_id)
    )
    pinned_cache = {row.node_id: row.payload for row in pinned_rows.all()}
    run_cache = {**pinned_cache, **(body.cache or {})}

    run_id = await start_run(
        workflow_id,
        graph,
        latest.version,
        workflow_version_id=None,
        mode=body.mode,
        targets=body.targets,
        cache=run_cache or None,
        parameters=body.parameters,
    )
    return RunCreated(run_id=run_id)


@router.get("/workflows/{workflow_id}/runs", response_model=list[RunInfo])
async def list_runs(
    workflow_id: str, session: AsyncSession = Depends(get_session)
):
    result = await session.scalars(
        select(Run)
        .where(Run.workflow_id == workflow_id)
        .options(selectinload(Run.node_runs))
        .order_by(Run.started_at.desc())
        .limit(50)
    )
    return list(result.all())


@router.get("/runs", response_model=list[RunListItem])
async def list_all_runs(
    workflow_id: str | None = None,
    status: str | None = None,
    trigger_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[RunListItem]:
    """Cross-workflow run listing for the Executions page.

    Pages and filters by workflow, status, trigger, and time window. Returns the
    compact ``RunListItem`` (no node_runs) — clients fetch ``GET /runs/{id}``
    for the per-node breakdown + logs.
    """
    stmt = select(Run, Workflow.name).join(Workflow, Run.workflow_id == Workflow.id)
    if workflow_id is not None:
        stmt = stmt.where(Run.workflow_id == workflow_id)
    if status is not None:
        stmt = stmt.where(Run.status == status)
    if trigger_type is not None:
        stmt = stmt.where(Run.trigger_type == trigger_type)
    if since is not None:
        stmt = stmt.where(Run.started_at >= since)
    if until is not None:
        stmt = stmt.where(Run.started_at <= until)
    stmt = stmt.order_by(Run.started_at.desc()).limit(limit).offset(offset)

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
            mode=run.mode,
            status=run.status,
            trigger_type=run.trigger_type,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
        for run, workflow_name in rows
    ]


@router.get("/runs/{run_id}", response_model=RunInfo)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await session.get(Run, run_id, options=[selectinload(Run.node_runs)])
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


_TRIGGER_TYPES = {"manual_trigger", "webhook_trigger", "schedule_trigger"}


def _forward_descendants(graph: dict, seeds: set[str]) -> set[str]:
    """Return ``seeds`` plus every node reachable forward via edges."""
    by_source: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        src = edge.get("source")
        tgt = edge.get("target")
        if src and tgt:
            by_source.setdefault(src, []).append(tgt)
    visited: set[str] = set(seeds)
    queue = list(seeds)
    while queue:
        nid = queue.pop()
        for nxt in by_source.get(nid, []):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return visited


async def _load_run_and_workflow(
    session: AsyncSession, run_id: str
) -> tuple[Run, Workflow]:
    run = await session.get(Run, run_id, options=[selectinload(Run.node_runs)])
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == run.workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Workflow has been deleted"
        )
    return run, workflow


@router.post(
    "/runs/{run_id}/rerun",
    response_model=RunCreated,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def rerun_run(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunCreated:
    """Start a fresh run of the same workflow, replaying the prior trigger input."""
    run, workflow = await _load_run_and_workflow(session, run_id)
    graph, version, version_id = await _graph_for_run(session, run, workflow)

    # Replay parameters by reading the trigger node's recorded output.
    trigger_node = next(
        (n for n in graph.get("nodes", []) if n.get("type") in _TRIGGER_TYPES),
        None,
    )
    parameters: dict | None = None
    if trigger_node is not None:
        trigger_run = next(
            (nr for nr in run.node_runs if nr.node_id == trigger_node["id"]),
            None,
        )
        if trigger_run and isinstance(trigger_run.output, dict):
            value = trigger_run.output.get("main")
            if isinstance(value, dict):
                parameters = value

    new_run_id = await start_run(
        workflow.id,
        graph,
        version,
        workflow_version_id=version_id,
        deployment_id=run.deployment_id,
        mode=run.mode,
        trigger_type=run.trigger_type,
        parameters=parameters,
    )
    return RunCreated(run_id=new_run_id)


@router.post(
    "/runs/{run_id}/retry",
    response_model=RunCreated,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def retry_from_failure(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunCreated:
    """Re-run only the failed node + its descendants, reusing successful outputs."""
    run, workflow = await _load_run_and_workflow(session, run_id)
    graph, version, version_id = await _graph_for_run(session, run, workflow)

    failed_ids = {nr.node_id for nr in run.node_runs if nr.status == "error"}
    if not failed_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No failed nodes to retry on this run.",
        )

    # Reuse every successful upstream node's output via the engine's cache.
    cache: dict[str, dict] = {}
    for nr in run.node_runs:
        if nr.status == "success" and isinstance(nr.output, dict):
            cache[nr.node_id] = nr.output

    targets = sorted(_forward_descendants(graph, failed_ids))

    new_run_id = await start_run(
        workflow.id,
        graph,
        version,
        workflow_version_id=version_id,
        deployment_id=run.deployment_id,
        mode=run.mode,
        trigger_type=run.trigger_type,
        targets=targets,
        cache=cache or None,
    )
    return RunCreated(run_id=new_run_id)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunCancelResponse,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def cancel_workflow_run(run_id: str) -> RunCancelResponse:
    result = await cancel_run(run_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return RunCancelResponse(run_id=run_id, status=result)


@router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: str) -> None:
    if settings.auth_required:
        token = websocket.query_params.get("token", "")
        if verify_token(token) is None:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    try:
        async for event in broker.subscribe(run_id):
            await websocket.send_json(event)
    finally:
        await websocket.close()
