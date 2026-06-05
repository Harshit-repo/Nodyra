"""Public (unauthenticated) chat endpoints for the hosted chat page and widget.

Only workflows whose Chat Trigger has ``public_access = true`` are served here.
Requests that don't match return 404 so private workflows are not discoverable.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Workflow
from app.schemas import ChatPublicConfig, ChatTurnRequest, ChatTurnResponse
from app.services.chat_service import NoChatTriggerError, WorkflowNotFoundError, run_chat_turn

router = APIRouter(prefix="/chat/p", tags=["chat-public"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _chat_trigger_params(graph: dict | None) -> dict | None:
    """Return the params dict of the first chat_trigger node, or None."""
    for node in (graph or {}).get("nodes", []):
        if node.get("type") == "chat_trigger":
            return node.get("params") or {}
    return None


async def _load_graph(workflow_id: str) -> dict | None:
    async with SessionLocal() as session:
        wf = (
            await session.scalars(
                select(Workflow)
                .options(selectinload(Workflow.versions))
                .where(Workflow.id == workflow_id)
            )
        ).first()
        if wf is None:
            return None
        if getattr(wf, "draft_graph", None):
            return wf.draft_graph
        if wf.versions:
            return wf.versions[-1].graph
        return None


@router.get("/{workflow_id}", response_model=ChatPublicConfig)
async def get_public_chat_config(workflow_id: str) -> ChatPublicConfig:
    graph = await _load_graph(workflow_id)
    if graph is None:
        raise _NOT_FOUND
    params = _chat_trigger_params(graph)
    if not params or not params.get("public_access"):
        raise _NOT_FOUND
    return ChatPublicConfig(
        workflow_id=workflow_id,
        title=str(params.get("title") or ""),
        placeholder=str(params.get("input_placeholder") or ""),
        initial_message=str(params.get("initial_message") or ""),
    )


@router.post("/{workflow_id}", response_model=ChatTurnResponse)
async def public_chat_turn(workflow_id: str, body: ChatTurnRequest) -> ChatTurnResponse:
    graph = await _load_graph(workflow_id)
    if graph is None:
        raise _NOT_FOUND
    params = _chat_trigger_params(graph)
    if not params or not params.get("public_access"):
        raise _NOT_FOUND
    try:
        result = await run_chat_turn(
            workflow_id, body.message, body.session_id, prefer_draft=True
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoChatTriggerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return ChatTurnResponse(
        run_id=result.run_id,
        reply=result.reply,
        session_id=result.session_id,
        status=result.status,
    )
