"""Terminal persistence for a finished run (split out of runner.py, A2).

Everything here is invoked by ``runner._execute_run`` with an explicit
``session_factory`` — the runner module's (test-swappable) ``SessionLocal``
— so this module holds no DB state of its own.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import NodeRun, Run, RunApproval, RunEvent
from app.services import queue as run_queue
from nodyra.serialization import truncate_serialized_value

logger = logging.getLogger(__name__)

# Bound on in-memory agent/guardrail events accumulated per run. Each event is
# capped at max_output_bytes by _cap_output, so worst-case RSS per run is
# MAX_RUN_EVENTS * max_output_bytes. Default: 2000 * 256KB = ~500MB ceiling,
# but in practice most events are tiny. A run that hits this cap gets a
# sentinel warning event appended so the user knows events were dropped.
_MAX_RUN_EVENTS = 2_000

AGENT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "agent_action_requested",
        "agent_tool_started",
        "agent_tool_approval_required",
        "agent_tool_auto_approved",
        "agent_tool_finished",
        "agent_action_completed",
        "agent_tool_approval_decided",
    }
)
GUARDRAIL_EVENT_TYPES: frozenset[str] = frozenset(
    {"guardrail_blocked", "guardrail_redacted"}
)


def _epoch_to_dt(value: float | None) -> datetime | None:
    """D-12: convert epoch-seconds float from the engine into a timezone-aware datetime."""
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def _approval_key(event: dict[str, Any]) -> str:
    """Stable key for idempotent approval rows across local/remote streams."""
    raw = "|".join(
        [
            str(event.get("agent_node_id") or event.get("node_id") or ""),
            str(event.get("step") or 0),
            str(event.get("tool_call_id") or ""),
            str(event.get("tool_name") or ""),
        ]
    )
    return raw[:240]


async def _upsert_run_approval(
    session: AsyncSession,
    *,
    run_id: str,
    event: dict[str, Any],
    event_ts: datetime,
) -> None:
    """Create/update the operator approval record represented by an agent event."""
    event_type = str(event.get("type") or "")
    if event_type not in {"agent_tool_approval_required", "agent_tool_auto_approved"}:
        return

    key = _approval_key(event)
    approval = await session.scalar(
        select(RunApproval).where(
            RunApproval.run_id == run_id,
            RunApproval.approval_key == key,
        )
    )
    arguments = event.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    status = "approved" if event_type == "agent_tool_auto_approved" else "pending"
    max_steps_raw = event.get("max_steps")
    max_steps = int(max_steps_raw) if max_steps_raw is not None else None
    reason = (
        "Auto-approved by AI Agent setting."
        if event_type == "agent_tool_auto_approved"
        else ""
    )

    if approval is None:
        approval = RunApproval(
            run_id=run_id,
            approval_key=key,
            status=status,
            node_id=event.get("node_id"),
            agent_node_id=event.get("agent_node_id"),
            step=int(event.get("step") or 0),
            max_steps=max_steps,
            tool_call_id=str(event.get("tool_call_id") or ""),
            tool_name=str(event.get("tool_name") or ""),
            arguments=arguments,
            message=str(event.get("message") or ""),
            requested_at=event_ts,
            resolved_at=event_ts if status == "approved" else None,
            resolved_by="auto" if status == "approved" else None,
            reason=reason,
        )
        session.add(approval)
        return

    approval.arguments = arguments
    approval.message = str(event.get("message") or approval.message or "")
    if approval.status == "pending" and status == "approved":
        approval.status = "approved"
        approval.resolved_at = event_ts
        approval.resolved_by = "auto"
        approval.reason = reason


async def _upsert_run_approval_in_memory(
    session: AsyncSession,
    *,
    run_id: str,
    event: dict[str, Any],
    event_ts: datetime,
    existing: dict[str, RunApproval],
) -> None:
    """Same as ``_upsert_run_approval`` but uses a pre-loaded ``existing`` dict
    to avoid the per-event SELECT (P1-9)."""
    event_type = str(event.get("type") or "")
    if event_type not in {"agent_tool_approval_required", "agent_tool_auto_approved"}:
        return

    key = _approval_key(event)
    approval = existing.get(key)  # already loaded in bulk
    arguments = event.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    status = "approved" if event_type == "agent_tool_auto_approved" else "pending"
    max_steps_raw = event.get("max_steps")
    max_steps = int(max_steps_raw) if max_steps_raw is not None else None
    reason = (
        "Auto-approved by AI Agent setting."
        if event_type == "agent_tool_auto_approved"
        else ""
    )

    if approval is None:
        approval = RunApproval(
            run_id=run_id,
            approval_key=key,
            status=status,
            node_id=event.get("node_id"),
            agent_node_id=event.get("agent_node_id"),
            step=int(event.get("step") or 0),
            max_steps=max_steps,
            tool_call_id=str(event.get("tool_call_id") or ""),
            tool_name=str(event.get("tool_name") or ""),
            arguments=arguments,
            message=str(event.get("message") or ""),
            requested_at=event_ts,
            resolved_at=event_ts if status == "approved" else None,
            resolved_by="auto" if status == "approved" else None,
            reason=reason,
        )
        session.add(approval)
        existing[key] = approval  # cache for later resume_state writes
        return

    approval.arguments = arguments
    approval.message = str(event.get("message") or approval.message or "")
    if approval.status == "pending" and status == "approved":
        approval.status = "approved"
        approval.resolved_at = event_ts
        approval.resolved_by = "auto"
        approval.reason = reason


def _maybe_truncate(value: Any, cap: int) -> Any:
    if value is None:
        return value
    return truncate_serialized_value(value, cap)


def _cap_output(value: Any, cap: int | None = None) -> Any:
    """Bound the size of a persisted NodeRun.output payload.

    Outputs are ``{port: value}`` dicts; cap each port independently so a
    single fat port doesn't drop the others. Anything past ``cap`` becomes
    ``{_truncated, size_bytes, preview}``. Falls back to
    ``settings.max_output_bytes`` when no explicit cap is supplied.
    """
    if cap is None:
        cap = settings.max_output_bytes
    if not cap or cap <= 0 or value is None:
        return value
    if isinstance(value, dict):
        return {port: _maybe_truncate(v, cap) for port, v in value.items()}
    return _maybe_truncate(value, cap)


def _cap_logs(logs: Any, cap: int | None = None) -> Any:
    """Bound the total bytes of persisted logs the same way as outputs."""
    if cap is None:
        cap = settings.max_output_bytes
    if not cap or cap <= 0 or not isinstance(logs, list):
        return logs
    total = 0
    kept: list[str] = []
    for line in logs:
        s = line if isinstance(line, str) else str(line)
        total += len(s) + 1  # newline overhead
        if total > cap:
            kept.append(f"… (log truncated at {cap} bytes)")
            break
        kept.append(s)
    return kept


def _contains_unrestorable_object(value: Any) -> bool:
    if isinstance(value, dict):
        if (
            value.get("__nodyra_typed__") is True
            and value.get("type") == "object"
            and value.get("restorable") is False
        ):
            return True
        return any(_contains_unrestorable_object(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_unrestorable_object(item) for item in value)
    return False


def _graph_node_types(graph: dict) -> dict[str, str]:
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    out: dict[str, str] = {}
    if not isinstance(nodes, list):
        return out
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        node_type = node.get("type")
        if isinstance(node_id, str) and isinstance(node_type, str):
            out[node_id] = node_type
    return out


def _extract_webhook_response(
    graph: dict, node_events: dict[str, dict]
) -> dict | None:
    """Pull the response a respond_to_webhook node recorded, if any.

    Returns the ``{status, headers, body, content_type}`` dict from the first
    executed ``respond_to_webhook`` node, or ``None``. Persisted to
    ``runs.webhook_response`` so a waiting webhook handler (Respond Node mode)
    can return it from any replica (the DB is shared).
    """
    for node in (graph or {}).get("nodes", []):
        if node.get("type") != "respond_to_webhook":
            continue
        event = node_events.get(node.get("id"))
        if not event:
            continue
        outputs = event.get("outputs") or {}
        response = outputs.get("main")
        if isinstance(response, dict):
            return response
    return None


def _run_error_message(event: dict[str, Any]) -> str | None:
    message = event.get("error") or event.get("message")
    return str(message) if message else None


def _extract_run_level_error(
    *,
    status: str,
    node_run_records: dict[tuple[str, tuple], dict],
    run_events: deque[dict[str, Any]],
) -> str | None:
    """Return the durable run-level failure reason, when no node owns it."""
    if status == "timed_out":
        for event in node_run_records.values():
            message = str(event.get("error") or "")
            if "timed out" in message.lower():
                return message
        return "workflow run timed out"
    if status != "error":
        return None
    if any(event.get("error") for event in node_run_records.values()):
        return None
    for item in reversed(run_events):
        event = item.get("event")
        if isinstance(event, dict) and event.get("type") == "run_error":
            return _run_error_message(event)
    return None


async def persist_run_outcome(
    session_factory,
    *,
    run_id: str,
    status: str,
    graph_dict: dict,
    node_events: dict[str, dict],
    node_run_records: dict[tuple[str, tuple], dict],
    run_events: deque[dict[str, Any]],
    output_cap: int,
) -> None:
    """Write the terminal state of a run: Run row, NodeRun records, RunEvents,
    approval upserts/resume-state, and the durable-queue mirror transition.

    Failures degrade to a minimal status-only update so a flaky DB never
    leaves a run stuck in ``running``.
    """
    try:
        async with session_factory() as session:
            run = await session.get(Run, run_id)
            if run is not None:
                from app.services.run_lifecycle import require_transition

                require_transition(run.status, status, run_id=run_id)
                run.status = status
                run.finished_at = None if status == "waiting" else datetime.now(UTC)
                # ADR-0003: keep run-owned failures on runs.error so event
                # retention cannot erase the run summary.
                run.error = _extract_run_level_error(
                    status=status,
                    node_run_records=node_run_records,
                    run_events=run_events,
                )
                # Clear checkpoint on terminal states (keep for "waiting").
                if status != "waiting":
                    run.checkpoint = None
                webhook_response = _extract_webhook_response(graph_dict, node_events)
                if webhook_response is not None:
                    run.webhook_response = webhook_response
            # T-08: bulk insert all NodeRun records in one statement rather than
            # one session.add() per record. A 1000-iteration loop produced 1000+
            # individual INSERTs; this is now a single multi-row INSERT.
            from app.services.output_store import maybe_offload_output

            node_run_rows = [
                {
                    "run_id": run_id,
                    "node_id": node_id,
                    "status": event.get("status", "unknown"),
                    # Cap FIRST, then offload the bounded survivor. Offloading
                    # first would spill the full (uncapped) output to disk and
                    # leave only a tiny marker for the cap to see — defeating the
                    # output cap and letting an arbitrarily large payload hit the
                    # store. Capping first bounds what the offload store persists.
                    "output": maybe_offload_output(
                        _cap_output(event.get("outputs"), output_cap),
                        run_id=run_id,
                        node_id=node_id,
                    ),
                    "error": event.get("error"),
                    "logs": _cap_logs(event.get("logs"), output_cap),
                    "debug": event.get("debug"),
                    "started_at": _epoch_to_dt(event.get("started_at")),
                    "finished_at": _epoch_to_dt(event.get("finished_at")),
                    "duration_ms": event.get("duration_ms"),
                    "iteration_path": event.get("iteration_path"),
                }
                for (node_id, _path), event in node_run_records.items()
            ]
            if node_run_rows:
                await session.execute(insert(NodeRun), node_run_rows)
            # Bulk insert RunEvent rows (P1-8).  A run with 2000 events
            # previously issued 2000 individual INSERTs; now it's one call.
            run_event_rows = [
                {
                    "run_id": run_id,
                    "event_type": str(item["event"].get("type") or ""),
                    "sequence": int(item["sequence"]),
                    "ts": item["ts"],
                    "node_id": item["event"].get("node_id"),
                    "agent_node_id": item["event"].get("agent_node_id"),
                    "payload": _cap_output(item["event"], output_cap),
                }
                for item in run_events
            ]
            if run_event_rows:
                await session.execute(insert(RunEvent), run_event_rows)
            # Pre-load all existing approvals for this run in a single query
            # (P1-9), then upsert in memory.  Previously each event did its
            # own SELECT, yielding N+1 on top of the N writes.
            existing_approvals: dict[str, RunApproval] = {}
            event_keys: set[str] = set()
            for item in run_events:
                key = _approval_key(item["event"])
                if key:
                    event_keys.add(key)
            if event_keys:
                rows = (
                    await session.scalars(
                        select(RunApproval).where(
                            RunApproval.run_id == run_id,
                            RunApproval.approval_key.in_(event_keys),
                        )
                    )
                ).all()
                existing_approvals = {a.approval_key: a for a in rows}
            for item in run_events:
                event = item["event"]
                event_ts = item["ts"]
                await _upsert_run_approval_in_memory(
                    session,
                    run_id=run_id,
                    event=event,
                    event_ts=event_ts,
                    existing=existing_approvals,
                )
            # Apply resume_state updates from node_events (second pass).
            # The approvals were already loaded above, so no extra queries.
            for event in node_events.values():
                debug = event.get("debug")
                if not isinstance(debug, dict):
                    continue
                resume_state = debug.get("agent_approval_state")
                if not isinstance(resume_state, dict):
                    continue
                approval_key = str(resume_state.get("approval_key") or "")
                if not approval_key:
                    continue
                approval = existing_approvals.get(approval_key)
                if approval is not None:
                    approval.resume_state = resume_state
            # Mirror the run outcome onto the durable queue entry so the
            # queue is the single source of truth for orchestration state.
            if status == "success":
                await run_queue.complete(session, run_id=run_id)
            elif status == "waiting":
                await run_queue.wait_for_approval(session, run_id=run_id)
            elif status == "cancelled":
                await run_queue.cancel(session, run_id=run_id)
            else:
                failure_error = (
                    run.error
                    if run is not None and run.error
                    else f"run finished with status={status}"
                )
                await run_queue.fail(
                    session,
                    run_id=run_id,
                    retryable=False,
                    error=failure_error,
                )
            # C3: accumulate compute seconds + node_runs for terminal runs
            # ("waiting" resumes later and lands here again at the real end).
            if settings.multi_tenancy_enabled and status != "waiting":
                from app.services import metering  # noqa: PLC0415

                try:
                    await metering.record_run_completion(session, run_id)
                except Exception:  # noqa: BLE001 - metering never fails a run
                    logger.exception("run_id=%s metering failed", run_id)
            if run is not None and status != "waiting" and run.batch_id:
                from app.services.run_batches import reconcile_batch  # noqa: PLC0415

                await reconcile_batch(session, run.batch_id)
            await session.commit()

    except Exception:  # noqa: BLE001
        logger.exception(
            "run_id=%s DB persist failed; attempting minimal status update", run_id
        )
        try:
            async with session_factory() as _s:
                _r = await _s.get(Run, run_id)
                if _r is not None and _r.status not in (
                    "success",
                    "error",
                    "timed_out",
                    "cancelled",
                    "waiting",
                ):
                    _r.status = status
                    _r.finished_at = datetime.now(UTC)
                    _r.checkpoint = None
                    await _s.commit()
        except Exception:  # noqa: BLE001
            logger.exception("run_id=%s minimal status fallback also failed", run_id)
