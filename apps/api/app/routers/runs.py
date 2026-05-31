import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, WebSocket, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_session
from app.models import NodeRun, PinnedData, Run, RunQueueEntry, Workflow, WorkflowVersion
from app.schemas import (
    RunCancelResponse,
    RunCreated,
    RunDebugSnapshot,
    RunInfo,
    RunListItem,
    PageResponse,
    RunReplayRequest,
    RunReplayResponse,
    RunRequest,
    RunTimeline,
    RunTimelineEvent,
)
from app.security import require_permission
from app.services.crypto import verify_token
from app.services.events import broker
from app.services.graph_utils import (
    first_trigger_node,
    forward_descendants,
)
from app.services.runner import cancel_run, start_run
from app.services import queue as run_queue

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
    body: RunRequest | None = Body(default=None),
    use_draft: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
):
    workflow = await session.get(
        Workflow, workflow_id, options=[selectinload(Workflow.versions)]
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")

    if body is None:
        body = RunRequest()
    latest = workflow.versions[-1]
    if use_draft:
        graph = _draft_graph(workflow)
        version_id: str | None = None
        version_number = latest.version
    else:
        graph = latest.graph or EMPTY_GRAPH
        version_id = latest.id
        version_number = latest.version
    pinned_rows = await session.scalars(
        select(PinnedData).where(PinnedData.workflow_id == workflow_id)
    )
    pinned_cache = {row.node_id: row.payload for row in pinned_rows.all()}
    run_cache = {**pinned_cache, **(body.cache or {})}

    try:
        run_id = await start_run(
            workflow_id,
            graph,
            version_number,
            workflow_version_id=version_id,
            mode=body.mode,
            targets=body.targets,
            cache=run_cache or None,
            parameters=body.parameters or body.data,
            trigger_node_id=body.trigger_node_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return RunCreated(run_id=run_id)


@router.get("/workflows/{workflow_id}/runs", response_model=PageResponse[RunInfo])
async def list_runs(
    workflow_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    total = await session.scalar(
        select(func.count()).select_from(Run).where(Run.workflow_id == workflow_id)
    )
    result = await session.scalars(
        select(Run)
        .where(Run.workflow_id == workflow_id)
        .options(selectinload(Run.node_runs))
        .order_by(Run.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return PageResponse(items=list(result.all()), total=total or 0, limit=limit, offset=offset)


@router.get("/runs", response_model=PageResponse[RunListItem])
async def list_all_runs(
    workflow_id: str | None = None,
    status: str | None = None,
    trigger_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> PageResponse[RunListItem]:
    """Cross-workflow run listing for the Executions page.
    Pages and filters by workflow, status, trigger, and time window. Returns the
    compact ``RunListItem`` (no node_runs) — clients fetch ``GET /runs/{id}``
    for the per-node breakdown + logs.
    """
    # Build the base filter without ORDER/LIMIT/OFFSET for the count.
    base_stmt = select(Run).join(Workflow, Run.workflow_id == Workflow.id)
    if workflow_id is not None:
        base_stmt = base_stmt.where(Run.workflow_id == workflow_id)
    if status is not None:
        base_stmt = base_stmt.where(Run.status == status)
    if trigger_type is not None:
        base_stmt = base_stmt.where(Run.trigger_type == trigger_type)
    if since is not None:
        base_stmt = base_stmt.where(Run.started_at >= since)
    if until is not None:
        base_stmt = base_stmt.where(Run.started_at <= until)

    total = await session.scalar(
        select(func.count()).select_from(base_stmt.subquery())
    )

    stmt = (
        select(Run, Workflow.name)
        .join(Workflow, Run.workflow_id == Workflow.id)
    )
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
    items = [
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
    return PageResponse(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RunInfo)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await session.get(Run, run_id, options=[selectinload(Run.node_runs)])
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    workflow_name: str | None = None
    if run.workflow_id:
        wf = await session.get(Workflow, run.workflow_id)
        if wf is not None:
            workflow_name = wf.name
    # RunInfo is from_attributes but workflow_name is not on the ORM model;
    # build the response manually.
    from app.schemas import RunInfo as _RunInfo
    return _RunInfo(
        id=run.id,
        workflow_id=run.workflow_id,
        workflow_name=workflow_name,
        workflow_version=run.workflow_version,
        workflow_version_id=run.workflow_version_id,
        deployment_id=run.deployment_id,
        triggered_by_error_run_id=run.triggered_by_error_run_id,
        runner_pool_id=getattr(run, 'runner_pool_id', None),
        runner_id=getattr(run, 'runner_id', None),
        batch_id=getattr(run, 'batch_id', None),
        mode=run.mode,
        status=run.status,
        trigger_type=run.trigger_type,
        started_at=run.started_at,
        finished_at=run.finished_at,
        node_runs=run.node_runs,
    )


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
    trigger_node = first_trigger_node(graph)
    parameters: dict | None = None
    if trigger_node is not None:
        trigger_id = (
            trigger_node["id"] if isinstance(trigger_node, dict) else trigger_node.id
        )
        trigger_run = next(
            (nr for nr in run.node_runs if nr.node_id == trigger_id),
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

    targets = sorted(forward_descendants(graph, failed_ids))

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


@router.post(
    "/runs/{run_id}/replay",
    response_model=RunReplayResponse,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def replay_workflow_run(
    run_id: str,
    body: RunReplayRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> RunReplayResponse:
    """Re-queue a terminal run for another dispatch attempt.

    Eligible when the durable queue entry is ``failed``, ``dead_lettered``, or
    ``cancelled``. The next dispatch loop tick will pick it up; ``Run.status``
    is reset to ``queued`` so the UI reflects the pending replay immediately.

    When ``from_node_id`` is supplied (replay-from-failure), the engine is
    seeded with the prior run's successful upstream NodeRun outputs and
    execution is restricted to ``from_node_id`` plus its forward descendants.
    """
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
    )
    if entry is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Run has no durable queue entry to replay.",
        )
    previous = entry.status

    from_node_id = body.from_node_id if body is not None else None
    replay_cache: dict | None = None
    replay_targets: list[str] | None = None

    if from_node_id is not None:
        workflow = await session.get(
            Workflow, run.workflow_id, options=[selectinload(Workflow.versions)]
        )
        if workflow is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Workflow for run no longer exists."
            )
        graph: dict | None = None
        if run.workflow_version_id:
            version = await session.get(WorkflowVersion, run.workflow_version_id)
            if version is not None:
                graph = version.graph
        if not graph:
            graph = _draft_graph(workflow)

        node_ids = {n.get("id") for n in graph.get("nodes") or [] if isinstance(n, dict)}
        if from_node_id not in node_ids:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"from_node_id '{from_node_id}' is not a node in this workflow.",
            )

        # Targets: the failed node + everything downstream.
        replay_targets = sorted(forward_descendants(graph, {from_node_id}))

        # Cache: most-recent successful NodeRun outputs for every node that is
        # NOT in the replay set (i.e. the upstream ancestors). The engine's
        # ``_needed_nodes`` traversal stops at cached nodes, so ancestors of
        # cached nodes are skipped automatically.
        prior_runs = (
            await session.scalars(
                select(NodeRun)
                .where(NodeRun.run_id == run_id, NodeRun.status == "success")
                .order_by(NodeRun.finished_at.asc())
            )
        ).all()
        latest_outputs: dict[str, dict] = {}
        replay_set = set(replay_targets)
        for nr in prior_runs:
            if nr.node_id in replay_set:
                continue
            if isinstance(nr.output, dict):
                latest_outputs[nr.node_id] = nr.output  # later wins (most recent)
        replay_cache = latest_outputs or None

    replayed = await run_queue.replay(
        session, run_id=run_id, cache=replay_cache, targets=replay_targets
    )
    if replayed is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run queue entry is in '{previous}'; only failed/dead_lettered/"
            "cancelled entries can be replayed.",
        )
    run.status = "queued"
    run.finished_at = None
    await session.commit()
    return RunReplayResponse(run_id=run_id, previous_status=previous)


def _epoch_to_dt(value: float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


@router.get("/runs/{run_id}/timeline", response_model=RunTimeline)
async def run_timeline(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunTimeline:
    """Ordered lifecycle events for a single run.

    Composes ``Run`` start/finish, ``RunQueueEntry`` enqueue/lease/retry, and
    ``NodeRun`` start/finish into one chronological feed. Designed for the
    backpressure UI and replay/debug views; clients can render it directly
    without re-deriving timings from disparate records.
    """
    run = await session.get(Run, run_id, options=[selectinload(Run.node_runs)])
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
    )

    events: list[RunTimelineEvent] = []

    if entry is not None:
        events.append(
            RunTimelineEvent(
                type="enqueued",
                ts=entry.created_at,
                data={
                    "reason": entry.queue_reason or None,
                    "priority": entry.priority,
                    "max_attempts": entry.max_attempts,
                },
            )
        )
        if entry.leased_by is not None or entry.status in ("leased", "running"):
            events.append(
                RunTimelineEvent(
                    type="leased",
                    ts=entry.lease_expires_at,
                    data={
                        "leased_by": entry.leased_by,
                        "attempts": entry.attempts,
                    },
                )
            )
        if entry.status == "dead_lettered":
            events.append(
                RunTimelineEvent(
                    type="dead_lettered",
                    ts=entry.updated_at,
                    data={"last_error": entry.last_error},
                )
            )
        elif entry.status == "failed" and entry.last_error:
            events.append(
                RunTimelineEvent(
                    type="queue_failed",
                    ts=entry.updated_at,
                    data={
                        "last_error": entry.last_error,
                        "attempts": entry.attempts,
                    },
                )
            )

    events.append(
        RunTimelineEvent(
            type="started",
            ts=run.started_at,
            data={"trigger_type": run.trigger_type, "mode": run.mode},
        )
    )

    node_events: list[RunTimelineEvent] = []
    for nr in run.node_runs:
        node_events.append(
            RunTimelineEvent(
                type="node_started",
                ts=_epoch_to_dt(nr.started_at),
                data={"node_id": nr.node_id},
            )
        )
        node_events.append(
            RunTimelineEvent(
                type="node_finished",
                ts=_epoch_to_dt(nr.finished_at),
                data={
                    "node_id": nr.node_id,
                    "status": nr.status,
                    "duration_ms": nr.duration_ms,
                    "error": nr.error,
                },
            )
        )
    # Stable order: events with a timestamp sort first by time, then by type so
    # node_started precedes node_finished for the same instant; events without a
    # timestamp keep their list position at the end.
    node_events.sort(
        key=lambda e: (
            e.ts is None,
            e.ts or datetime.max.replace(tzinfo=UTC),
            0 if e.type == "node_started" else 1,
        )
    )
    events.extend(node_events)

    if run.finished_at is not None:
        terminal_type = {
            "success": "completed",
            "error": "failed",
            "cancelled": "cancelled",
        }.get(run.status, run.status)
        events.append(
            RunTimelineEvent(
                type=terminal_type,
                ts=run.finished_at,
                data={"status": run.status},
            )
        )

    return RunTimeline(run_id=run_id, status=run.status, events=events)


@router.get("/runs/{run_id}/debug-snapshot", response_model=RunDebugSnapshot)
async def run_debug_snapshot(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunDebugSnapshot:
    """Bundle the editor needs to "debug" a failed run.

    Returns the workflow graph as it ran, the first failed node id, every
    successful node's recorded output (so the editor can pin them as fake
    upstream values), and the error string for any failed nodes. The editor
    then drives ``POST /runs/{run_id}/replay`` with ``from_node_id`` set to
    actually re-execute from the failure point with this cache seeded.
    """
    run, workflow = await _load_run_and_workflow(session, run_id)
    graph, version, version_id = await _graph_for_run(session, run, workflow)

    failed_nodes = [nr for nr in run.node_runs if nr.status == "error"]
    failed_node_id = failed_nodes[0].node_id if failed_nodes else None

    upstream_cache: dict[str, Any] = {}
    node_errors: dict[str, str] = {}
    for nr in run.node_runs:
        if nr.status == "success" and nr.output is not None:
            upstream_cache[nr.node_id] = nr.output
        if nr.status == "error" and nr.error:
            node_errors[nr.node_id] = nr.error

    return RunDebugSnapshot(
        run_id=run.id,
        workflow_id=run.workflow_id,
        workflow_version=version,
        workflow_version_id=version_id,
        status=run.status,
        graph=graph,
        failed_node_id=failed_node_id,
        upstream_cache=upstream_cache,
        node_errors=node_errors,
    )


@router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: str) -> None:
    if settings.auth_required:
        # WebSocket upgrades cannot send custom headers in many browsers/clients,
        # so we accept the token via query param OR Authorization header.
        token = websocket.query_params.get("token", "")
        if not token:
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
        if verify_token(token) is None:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    try:
        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(30)
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break

        hb_task = asyncio.create_task(_heartbeat())
        try:
            async for event in broker.subscribe(run_id):
                await websocket.send_json(event)
        finally:
            hb_task.cancel()
    finally:
        await websocket.close()
