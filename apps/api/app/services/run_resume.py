"""Approval-driven resume of a waiting run and durable-execution checkpoint
reconstruction (split from runner.py, A2).

``resume_waiting_run_from_approval`` receives its session factory from the
runner module at call time so test monkeypatching of ``runner.SessionLocal``
keeps applying.

``build_durable_execution_state`` reconstructs the in-memory ``node_outputs``
dict from persisted ``NodeRun`` rows so a run interrupted by a server restart
can resume from where it left off — Temporal-style durability without Temporal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import NodeRun, Run, RunApproval, RunEvent, Workflow, WorkflowVersion
from app.services import queue as run_queue
from app.services.graph_utils import forward_descendants
from app.services.run_persistence import (
    _contains_unrestorable_object,
    _graph_node_types,
)
from noodle.ai_runtime import AgentActionRequest

logger = logging.getLogger(__name__)


async def resume_waiting_run_from_approval(
    session_factory, *, run_id: str, approval_id: str, approve_all: bool = False
) -> dict | None:
    """Requeue a waiting run using the stored approved agent action request.

    Returns the ``agent_resume_prepared`` event dict on success, or ``None``
    when the run can't be resumed.
    """
    async with session_factory() as session:
        approval = await session.scalar(
            select(RunApproval).where(
                RunApproval.run_id == run_id,
                RunApproval.id == approval_id,
            )
        )
        run = await session.get(Run, run_id)
        if (
            approval is None
            or run is None
            or approval.status not in {"approved", "rejected"}
            or run.status != "waiting"
            or not isinstance(approval.resume_state, dict)
        ):
            return None

        agent_node_id = str(
            approval.resume_state.get("agent_node_id")
            or approval.agent_node_id
            or approval.node_id
            or ""
        )
        request_state = approval.resume_state.get("request")
        if not agent_node_id or not isinstance(request_state, dict):
            return None

        request = AgentActionRequest.model_validate(request_state)
        if approval.status == "approved":
            approved_ids = set(request.approved_tool_call_ids or [])
            approved_ids.add(approval.tool_call_id)
            request.approved_tool_call_ids = sorted(approved_ids)
            if approve_all:
                request.allow_side_effects = True
        else:
            rejected_ids = set(request.rejected_tool_call_ids or [])
            rejected_ids.add(approval.tool_call_id)
            request.rejected_tool_call_ids = sorted(rejected_ids)

        workflow = await session.scalar(
            select(Workflow)
            .where(Workflow.id == run.workflow_id)
            .options(selectinload(Workflow.versions))
        )
        if workflow is None or not workflow.versions:
            return None

        graph_dict: dict | None = None
        if run.workflow_version_id:
            version_row = await session.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.id == run.workflow_version_id
                )
            )
            if version_row is not None:
                graph_dict = version_row.graph
        if not graph_dict:
            graph_dict = workflow.draft_graph or workflow.versions[-1].graph
        if not graph_dict:
            return None

        cache: dict[str, dict] = {}
        skipped_cache_nodes: list[dict[str, Any]] = []
        node_types = _graph_node_types(graph_dict)
        node_runs = (
            await session.scalars(
                select(NodeRun).where(
                    NodeRun.run_id == run_id,
                    NodeRun.status == "success",
                ).order_by(NodeRun.finished_at.desc())
            )
        ).all()
        for node_run in node_runs:
            if node_run.node_id == agent_node_id:
                continue
            output = node_run.output
            if not isinstance(output, dict):
                continue
            if _contains_unrestorable_object(output):
                skipped_cache_nodes.append(
                    {
                        "node_id": node_run.node_id,
                        "node_type": node_types.get(node_run.node_id, ""),
                        "reason": "unrestorable_output",
                        "output_ports": sorted(str(port) for port in output.keys()),
                    }
                )
                continue
            cache[node_run.node_id] = output

        resume_targets = sorted(forward_descendants(graph_dict, {agent_node_id}))
        replay_seed = {
            "cache": cache,
            "targets": resume_targets,
            "skipped_cache_nodes": skipped_cache_nodes,
            "agent_action_resume": {
                agent_node_id: request.model_dump(mode="json"),
            },
        }
        max_sequence = await session.scalar(
            select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
        )
        resume_event = {
            "type": "agent_resume_prepared",
            "approval_id": approval.id,
            "approval_key": approval.approval_key,
            "agent_node_id": agent_node_id,
            "tool_call_id": approval.tool_call_id,
            "tool_name": approval.tool_name,
            "approve_all": bool(approve_all),
            "cached_node_ids": sorted(cache.keys()),
            "skipped_cache_nodes": skipped_cache_nodes,
            "targets": resume_targets,
        }
        session.add(
            RunEvent(
                run_id=run_id,
                event_type="agent_resume_prepared",
                sequence=int(max_sequence or 0) + 1,
                ts=datetime.now(UTC),
                node_id=approval.node_id,
                agent_node_id=agent_node_id,
                payload=resume_event,
            )
        )
        entry = await run_queue.resume_waiting(
            session,
            run_id=run_id,
            replay_seed=replay_seed,
        )
        if entry is None:
            return None
        run.status = "queued"
        run.finished_at = None
        await session.commit()

    return resume_event


async def build_durable_execution_state(
    session_factory, *, run_id: str
) -> dict[str, dict] | None:
    """Reconstruct the in-memory ``node_outputs`` dict from persisted NodeRun rows.

    When a server restart interrupts a run, the next dispatch loop lease can
    call this to rebuild the execution cache from the DB. Successfully
    completed nodes become cache entries; their downstream nodes re-execute
    with the cached inputs already available, effectively resuming from where
    the run left off.

    Returns ``None`` when the run cannot be resumed (no completed nodes, or
    the run is in a terminal state).
    """
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        if run is None or run.status not in ("queued", "running"):
            return None

        node_runs = (
            await session.scalars(
                select(NodeRun).where(
                    NodeRun.run_id == run_id,
                    NodeRun.status == "success",
                )
            )
        ).all()

        from app.services.output_store import maybe_load_output

        cache: dict[str, dict] = {}
        for nr in node_runs:
            output = maybe_load_output(nr.output)
            if not isinstance(output, dict) or not output:
                continue
            # Don't seed outputs containing unrestorable objects (artifact
            # refs keyed to a dead process, etc.).
            if _contains_unrestorable_object(output):
                logger.debug(
                    "run_id=%s node_id=%s: skipping unrestorable output",
                    run_id, nr.node_id,
                )
                continue
            cache[nr.node_id] = dict(output)

        if not cache:
            return None

        logger.info(
            "run_id=%s: rebuilt durable state from %d completed node(s)",
            run_id, len(cache),
        )
        return cache
