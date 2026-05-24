from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import PinnedData, Run, Workflow
from app.schemas import (
    RunCancelResponse,
    RunCreated,
    RunInfo,
    RunListItem,
    RunRequest,
)
from app.services.events import broker
from app.services.runner import cancel_run, start_run

router = APIRouter(tags=["runs"])


@router.post(
    "/workflows/{workflow_id}/run",
    response_model=RunCreated,
    status_code=status.HTTP_202_ACCEPTED,
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
    pinned_rows = await session.scalars(
        select(PinnedData).where(PinnedData.workflow_id == workflow_id)
    )
    pinned_cache = {row.node_id: row.payload for row in pinned_rows.all()}
    run_cache = {**pinned_cache, **(body.cache or {})}

    run_id = await start_run(
        workflow_id,
        latest.graph or {"nodes": [], "edges": []},
        latest.version,
        mode=body.mode,
        targets=body.targets,
        cache=run_cache or None,
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


@router.post("/runs/{run_id}/cancel", response_model=RunCancelResponse)
async def cancel_workflow_run(run_id: str) -> RunCancelResponse:
    result = await cancel_run(run_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return RunCancelResponse(run_id=run_id, status=result)


@router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()
    try:
        async for event in broker.subscribe(run_id):
            await websocket.send_json(event)
    finally:
        await websocket.close()
