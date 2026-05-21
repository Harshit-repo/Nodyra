"""Workflow execution service.

Runs a workflow graph in-process with the Noodle engine, streams per-node
events to the broker for live editor updates, and persists the run.

Sets up the runtime context (``noodle.context.workflow_caller`` and
``call_chain``) so Execute-Workflow nodes can invoke sub-workflows by id.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from app.config import settings
from app.db import SessionLocal
from app.models import NodeRun, PinnedData, Run, Workflow
from app.services.events import broker
from app.services.runtime_pool import pool as runtime_pool
from noodle.context import call_chain, workflow_caller
from noodle.engine import execute
from noodle.models import WorkflowGraph
from noodle.sdk import registry as node_registry

TRIGGER_TYPES = ("manual_trigger", "webhook_trigger", "schedule_trigger")

_active_runs: dict[str, asyncio.Task[None]] = {}


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


async def _load_workflow_graph(
    session: AsyncSession, workflow_id: str
) -> tuple[dict, dict[str, dict]]:
    """Return (graph_dict, pinned_cache) for a workflow id."""
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise ValueError(f"workflow '{workflow_id}' not found")
    latest = workflow.versions[-1]
    pinned_rows = await session.scalars(
        select(PinnedData).where(PinnedData.workflow_id == workflow_id)
    )
    pinned = {row.node_id: row.payload for row in pinned_rows.all()}
    return latest.graph or {"nodes": [], "edges": []}, pinned


async def _call_sub_workflow(workflow_id: str, input_value: Any) -> Any:
    """Implementation of ``noodle.context.workflow_caller`` for the API.

    Loads the target workflow's graph, seeds its trigger node with the
    supplied input, runs it through the engine, and returns the output of its
    leaf node (or a dict keyed by leaf id when there are several).
    """
    chain = call_chain.get()
    if workflow_id in chain:
        raise RuntimeError(
            f"sub-workflow cycle detected — '{workflow_id}' is already running"
        )

    async with SessionLocal() as session:
        graph_dict, pinned_cache = await _load_workflow_graph(session, workflow_id)

    graph = WorkflowGraph.model_validate(graph_dict)
    sources = {edge.source for edge in graph.edges}

    cache: dict[str, dict] = dict(pinned_cache)
    trigger = next(
        (n for n in graph.nodes if n.type in TRIGGER_TYPES),
        None,
    )
    if trigger is not None and trigger.id not in cache:
        cache[trigger.id] = {"main": input_value if input_value is not None else {}}

    chain_token = call_chain.set(chain | {workflow_id})
    try:
        result = await execute(graph, node_registry, cache=cache or None)
    finally:
        call_chain.reset(chain_token)

    leaves = [
        nid
        for nid, run in result.nodes.items()
        if run.status == "success" and nid not in sources
    ]
    if len(leaves) == 1:
        return result.nodes[leaves[0]].outputs.get("main")
    if leaves:
        return {nid: result.nodes[nid].outputs.get("main") for nid in leaves}
    return None


async def start_run(
    workflow_id: str,
    graph: dict,
    version: int,
    *,
    mode: str = "manual",
    trigger_type: str = "manual",
    targets: list[str] | None = None,
    cache: dict[str, dict] | None = None,
) -> str:
    """Create a run record and launch execution in the background."""
    async with SessionLocal() as session:
        run = Run(
            workflow_id=workflow_id,
            workflow_version=version,
            mode=mode,
            trigger_type=trigger_type,
            status="running",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    if settings.run_synchronously:
        current = asyncio.current_task()
        if current is not None:
            _active_runs[run_id] = current
        try:
            await _execute_run(run_id, workflow_id, graph, targets, cache)
        finally:
            _active_runs.pop(run_id, None)
    else:
        task = asyncio.create_task(
            _execute_run(run_id, workflow_id, graph, targets, cache)
        )
        _active_runs[run_id] = task
        task.add_done_callback(lambda _task: _active_runs.pop(run_id, None))
    return run_id


async def cancel_run(run_id: str) -> str | None:
    """Cancel an active run, or mark a stale running record as cancelled."""
    task = _active_runs.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        return "cancelling"

    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return None
        if run.status == "running":
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
            await session.commit()
            broker.publish(
                run_id,
                {
                    "type": "run_cancelled",
                    "run_id": run_id,
                    "error": "Run cancelled",
                },
            )
            broker.publish(
                run_id,
                {"type": "run_finished", "run_id": run_id, "status": "cancelled"},
            )
        return run.status


async def shutdown_active_runs(timeout: float = 5.0) -> None:
    tasks = [task for task in _active_runs.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )


async def _execute_run(
    run_id: str,
    workflow_id: str,
    graph_dict: dict,
    targets: list[str] | None,
    cache: dict[str, dict] | None = None,
) -> None:
    node_events: dict[str, dict] = {}

    async def on_event(event: dict) -> None:
        clean = dict(event)
        if "outputs" in clean:
            clean["outputs"] = _json_safe(clean["outputs"])
        broker.publish(run_id, clean)
        if clean.get("type") == "node_finished":
            node_events[clean["node_id"]] = clean

    broker.publish(run_id, {"type": "run_started", "run_id": run_id})
    status = "success"

    try:
        if settings.use_subprocess_runner:
            env_id: str | None = None
            async with SessionLocal() as session:
                workflow = await session.get(Workflow, workflow_id)
                if workflow is not None:
                    env_id = workflow.environment_id
            chain_token = call_chain.set(frozenset({workflow_id}))
            caller_token = workflow_caller.set(_call_sub_workflow)
            try:
                status = await runtime_pool.dispatch(
                    env_id,
                    graph_dict,
                    cache,
                    targets,
                    on_event,
                    sub_workflow_caller=_call_sub_workflow,
                )
            finally:
                workflow_caller.reset(caller_token)
                call_chain.reset(chain_token)
        else:
            chain_token = call_chain.set(frozenset({workflow_id}))
            caller_token = workflow_caller.set(_call_sub_workflow)
            try:
                graph = WorkflowGraph.model_validate(graph_dict)
                result = await execute(
                    graph,
                    node_registry,
                    cache=cache,
                    targets=targets,
                    on_event=on_event,
                )
                status = str(result.status)
            finally:
                workflow_caller.reset(caller_token)
                call_chain.reset(chain_token)
    except asyncio.CancelledError:
        status = "cancelled"
        broker.publish(
            run_id,
            {"type": "run_cancelled", "run_id": run_id, "error": "Run cancelled"},
        )
    except Exception as exc:  # noqa: BLE001 - report any execution failure
        status = "error"
        broker.publish(
            run_id,
            {
                "type": "run_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )

    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is not None:
            run.status = status
            run.finished_at = datetime.now(UTC)
            for node_id, event in node_events.items():
                session.add(
                    NodeRun(
                        run_id=run_id,
                        node_id=node_id,
                        status=event.get("status", "unknown"),
                        output=event.get("outputs"),
                        error=event.get("error"),
                        logs=event.get("logs"),
                        started_at=event.get("started_at"),
                        finished_at=event.get("finished_at"),
                        duration_ms=event.get("duration_ms"),
                    )
                )
            await session.commit()

    broker.publish(
        run_id, {"type": "run_finished", "run_id": run_id, "status": status}
    )
