from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import PinnedData, Run, Workflow
from app.schemas import RunCreated, RunInfo, RunRequest
from app.services.events import broker
from app.services.runner import start_run

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

    run_id = await start_run(
        workflow_id,
        latest.graph or {"nodes": [], "edges": []},
        latest.version,
        mode=body.mode,
        targets=body.targets,
        cache=pinned_cache or None,
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


@router.get("/runs/{run_id}", response_model=RunInfo)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await session.get(Run, run_id, options=[selectinload(Run.node_runs)])
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()
    try:
        async for event in broker.subscribe(run_id):
            await websocket.send_json(event)
    finally:
        await websocket.close()
