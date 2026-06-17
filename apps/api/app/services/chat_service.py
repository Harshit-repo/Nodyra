"""Run one conversational turn against a workflow's Chat Trigger.

Surface-agnostic core reused by the editor endpoint (Phase 1) and, later, the
hosted chat page and embeddable widget. Mirrors how ``triggers.dispatch_webhook``
seeds a trigger node's output and starts a run, then reuses the synchronous
"Last Node" wait/extract helpers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Workflow
from app.services.runner import start_run
from app.services.triggers import _await_run_terminal, _last_node_output


class ChatError(Exception):
    """Base for chat-turn failures."""


class NoChatTriggerError(ChatError):
    """Workflow has no chat_trigger node (or has no graph)."""


class WorkflowNotFoundError(ChatError):
    """Workflow id does not exist."""


@dataclass
class ChatTurnResult:
    run_id: str | None
    reply: str
    session_id: str
    status: str  # "success" | "error" | "timeout"


def _extract_reply(value: object) -> str:
    if isinstance(value, dict):
        for key in ("answer", "text", "output"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    if value is None:
        return ""
    return str(value)


def _find_chat_trigger(graph: dict) -> str | None:
    for node in graph.get("nodes", []):
        if node.get("type") == "chat_trigger":
            node_id = node.get("id")
            return str(node_id) if node_id is not None else None
    return None


async def run_chat_turn(
    workflow_id: str,
    message: str,
    session_id: str,
    *,
    prefer_draft: bool = True,
    timeout: float = 120.0,
) -> ChatTurnResult:
    run_id, session_id = await start_chat_turn(
        workflow_id, message, session_id, prefer_draft=prefer_draft
    )
    return await await_chat_result(run_id, session_id, timeout=timeout)


async def start_chat_turn(
    workflow_id: str,
    message: str,
    session_id: str,
    *,
    prefer_draft: bool = True,
) -> tuple[str, str]:
    """Seed the Chat Trigger and start a run, returning ``(run_id, session_id)``.

    Unlike :func:`run_chat_turn`, this returns as soon as the run is scheduled
    so callers can stream the run's live events (agent tool calls, node
    progress) over the run WebSocket before fetching the final reply with
    :func:`await_chat_result`.
    """
    async with SessionLocal() as session:
        workflow = (
            await session.scalars(
                select(Workflow)
                .options(selectinload(Workflow.versions))
                .where(Workflow.id == workflow_id)
            )
        ).first()
        if workflow is None:
            raise WorkflowNotFoundError("workflow not found")
        graph: dict | None = None
        version_number = 1
        version_id: str | None = None
        if prefer_draft and getattr(workflow, "draft_graph", None):
            graph = workflow.draft_graph
            if workflow.versions:
                version_number = workflow.versions[-1].version
                version_id = workflow.versions[-1].id
        elif workflow.versions:
            latest = workflow.versions[-1]
            graph = latest.graph or {}
            version_number = latest.version
            version_id = latest.id
        if not graph:
            raise NoChatTriggerError("workflow has no graph")
        trigger_id = _find_chat_trigger(graph)
        if trigger_id is None:
            raise NoChatTriggerError("workflow has no Chat Trigger node")

    payload = {"chatInput": message, "sessionId": session_id}
    run_id = await start_run(
        workflow_id,
        graph,
        version_number,
        workflow_version_id=version_id,
        mode="test" if prefer_draft else "production",
        trigger_type="chat",
        cache={trigger_id: {"main": payload}},
        trigger_node_id=trigger_id,
    )
    return run_id, session_id


async def await_chat_result(
    run_id: str,
    session_id: str,
    *,
    timeout: float = 120.0,
) -> ChatTurnResult:
    """Wait for a started chat run to finish and extract the reply."""
    status = await _await_run_terminal(run_id, timeout)
    if status is None:
        return ChatTurnResult(
            run_id, "The workflow did not respond in time.", session_id, "timeout"
        )
    if status != "success":
        return ChatTurnResult(
            run_id,
            "The workflow run failed. Open the run to see what happened.",
            session_id,
            "error",
        )
    async with SessionLocal() as session:
        body = await _last_node_output(session, run_id)
    return ChatTurnResult(run_id, _extract_reply(body), session_id, "success")
