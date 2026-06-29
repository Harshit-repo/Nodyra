"""Autonomous build-execute-diagnose-fix loop (MS4 Slice 4D).

The ``run_agentic_build_loop`` coroutine orchestrates up to *N* iterations:
  1. AI drafts or repairs the workflow graph (mode ``"draft"`` / ``"refine"``).
  2. Saves the updated graph to the workflow's draft field.
  3. Starts a test run with the provided test data.
  4. Polls until the run completes, fails, or a timeout is reached.
  5. On success => emit ``converged`` and return.
  6. On failure => extract failing nodes and loop again.

Every meaningful step emits an SSE-style event dict via ``event_callback`` so the
calling router can stream it to the client.  A shared ``cancel_event`` signals
the loop to stop when the SSE client disconnects.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Run, Workflow
from app.schemas import AiWorkflowDraftRequest
from app.services.ai_builder import build_workflow_draft
from app.services.runner import start_run

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_agentic_build_loop(
    workflow_id: str,
    goal: str,
    test_data: dict | None,
    *,
    max_iterations: int,
    org_id: str,
    event_callback: Callable[[dict], Awaitable[None]],
    cancel_event: asyncio.Event,
) -> dict:
    """Run the build -> execute -> diagnose -> fix loop.

    Parameters
    ----------
    workflow_id
        Target workflow.
    goal
        The user's natural-language instruction for what the workflow should do.
    test_data
        Optional sample input used for test runs.
    max_iterations
        Maximum number of build-run cycles (hard-capped at 5).
    org_id
        Current org id (used when creating test runs).
    event_callback
        Called with each SSE event dict before it is sent to the client.
    cancel_event
        Set by the caller when the SSE client disconnects.  Checked between
        each async step so the loop stops promptly.

    Returns
    -------
    The best graph produced (either the converged graph or the most recent
    iteration's graph when convergence was not reached).
    """
    current_graph = await _get_draft_graph(workflow_id)
    latest_graph: dict = current_graph
    failing_nodes: list[str] = []
    error_details: list[dict[str, str]] = []

    for iteration in range(1, max_iterations + 1):
        if cancel_event.is_set():
            logger.info("agentic build cancelled by client after iteration %d", iteration - 1)
            break

        action = "draft" if iteration == 1 else "fix"
        await event_callback({
            "type": "iteration_start",
            "iteration": iteration,
            "action": action,
        })

        # --- AI step: draft or fix ------------------------------------------------
        try:
            if iteration == 1:
                new_graph, explanation = await _ai_draft(goal, workflow_id)
            else:
                new_graph, explanation = await _ai_fix(
                    goal, workflow_id, current_graph, failing_nodes, error_details,
                )
        except Exception as exc:
            await event_callback({"type": "error", "message": f"LLM call failed: {exc}"})
            return latest_graph

        latest_graph = new_graph  # always update -- latest is the best we have

        await event_callback({
            "type": "graph_updated",
            "graph": new_graph,
            "explanation": explanation,
        })
        await _save_draft_graph(workflow_id, new_graph)

        # --- Test run -------------------------------------------------------------
        run_id = await _start_test_run(workflow_id, new_graph, test_data, org_id)
        await event_callback({"type": "run_started", "run_id": run_id})

        outcome = await _wait_for_run(
            run_id, timeout=120, cancel_event=cancel_event,
        )

        # The synchronous runner sets status "success" on completion; for async
        # orchestrated runs the queue sets it via the worker.  Both are terminal.
        if outcome.get("status") in ("success", "completed"):
            await event_callback({
                "type": "converged",
                "iterations": iteration,
                "final_graph": new_graph,
            })
            return new_graph

        failing_nodes, error_details = _extract_failures(outcome)

        await event_callback({
            "type": "run_failed",
            "run_id": run_id,
            "errors": error_details,
        })

        # If there's a fix_planned event, emit it so the frontend can show the diagnosis
        if failing_nodes:
            diagnosis = "; ".join(
                f"{e['node_id']}: {e['error']}" for e in error_details
            )
            await event_callback({
                "type": "fix_planned",
                "target_nodes": failing_nodes,
                "diagnosis": diagnosis,
            })

        current_graph = new_graph  # next iteration fixes from this point

    # Max iterations reached without convergence
    await event_callback({
        "type": "max_iterations_reached",
        "best_graph": latest_graph,
        "remaining_errors": error_details,
    })
    return latest_graph


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _get_draft_graph(workflow_id: str) -> dict:
    """Load the current draft graph from the workflow's ``draft_graph`` field."""
    async with SessionLocal() as session:
        wf = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
        return wf.draft_graph if wf and wf.draft_graph else {"nodes": [], "edges": []}


async def _save_draft_graph(workflow_id: str, graph: dict) -> None:
    """Persist the draft graph to the workflow row (same field the editor uses)."""
    async with SessionLocal() as session:
        wf = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
        if wf is not None:
            wf.draft_graph = graph
            await session.commit()


async def _ai_draft(
    goal: str,
    workflow_id: str,
) -> tuple[dict, str]:
    """Generate the initial graph from a goal string.

    Delegates to ``build_workflow_draft()`` with mode ``"draft"``.
    Returns ``(graph_dict, explanation)``.
    """
    async with SessionLocal() as session:
        result = await build_workflow_draft(
            session, workflow_id,
            AiWorkflowDraftRequest(prompt=goal, mode="draft"),
        )
    return result.graph.model_dump(), result.explanation or ""


async def _ai_fix(
    goal: str,
    workflow_id: str,
    current_graph: dict,
    failing_nodes: list[str],
    error_details: list[dict],
) -> tuple[dict, str]:
    """Fix failing nodes in the current graph.

    Uses ``mode="refine"`` with ``target_node_ids`` so the LLM focuses on the
    broken nodes.  Returns ``(graph_dict, explanation)``.
    """
    from noodle.models import WorkflowGraph

    error_summary = "; ".join(
        f"{e['node_id']}: {e['error']}" for e in error_details
    )
    fix_prompt = f"Goal: {goal}\n\nFix these failing nodes: {error_summary}"

    async with SessionLocal() as session:
        result = await build_workflow_draft(
            session, workflow_id,
            AiWorkflowDraftRequest(
                prompt=fix_prompt,
                mode="refine",
                current_graph=WorkflowGraph.model_validate(current_graph),
                target_node_ids=failing_nodes,
            ),
        )
    return result.graph.model_dump(), result.explanation or ""


async def _start_test_run(
    workflow_id: str,
    graph: dict,
    test_data: dict | None,
    org_id: str,
) -> str:
    """Launch a test run via the existing ``start_run`` service.

    Delegates to the same service-layer function used by
    ``POST /workflows/{id}/run`` so all normal run-creation logic (version
    resolution, queueing, execution) is shared.
    """
    # Load the workflow to determine the latest version number.
    async with SessionLocal() as session:
        wf = await session.scalar(
            select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.versions))
        )
    if wf is None:
        raise RuntimeError(f"Workflow {workflow_id} not found")

    version_number = wf.versions[-1].version if wf.versions else 1

    run_id = await start_run(
        workflow_id=workflow_id,
        graph=graph,
        version=version_number,
        parameters=test_data,
        mode="manual",
        trigger_type="manual",
    )
    return run_id


async def _wait_for_run(
    run_id: str,
    timeout: float,
    cancel_event: asyncio.Event,
) -> dict:
    """Poll run status until terminal (success/error/failed/cancelled) or timeout.

    Returns ``{"status": str, "node_results": dict, "error": str | None}``.
    ``node_results`` is a dict keyed by node_id whose values have the shape
    ``{"status": str, "output": ..., "error": str | None}``, reconstructed
    from the ``NodeRun`` relationship rows.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if cancel_event.is_set():
            return {"status": "cancelled", "node_results": {}, "error": None}
        async with SessionLocal() as session:
            run = await session.get(Run, run_id, options=[selectinload(Run.node_runs)])
        if run and run.status in ("success", "error", "failed", "cancelled"):
            node_results: dict[str, dict[str, Any]] = {}
            combined_error: str | None = None
            for nr in (run.node_runs or []):
                node_results[nr.node_id] = {
                    "status": nr.status,
                    "output": nr.output or {},
                    "error": nr.error or "",
                }
                if nr.status == "error" and not combined_error:
                    combined_error = nr.error
            return {
                "status": run.status,
                "node_results": node_results,
                "error": combined_error,
            }
        await asyncio.sleep(0.5)
    return {"status": "timeout", "node_results": {}, "error": "run timed out after 120 s"}


def _extract_failures(outcome: dict) -> tuple[list[str], list[dict[str, str]]]:
    """Extract failing node IDs and their error details from a run outcome dict."""
    failing_nodes: list[str] = []
    error_details: list[dict[str, str]] = []
    for node_id, result in (outcome.get("node_results") or {}).items():
        if isinstance(result, dict) and result.get("status") == "error":
            failing_nodes.append(node_id)
            error_details.append({
                "node_id": node_id,
                "error": result.get("error", ""),
            })
    return failing_nodes, error_details
