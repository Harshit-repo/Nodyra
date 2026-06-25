"""Hosted chat-page endpoints.

Only workflows whose Chat Trigger has ``public_access = true`` are served here.
Requests that don't match return 404 so private workflows are not discoverable.

Access modes controlled by the ``require_login`` param on the chat_trigger node:
  - ``require_login=True`` (default): visitor must supply a valid Noodle JWT.
  - ``require_login=False`` with ``chat_token`` set: visitor must supply the
    matching token as ``?token=<value>`` in the POST URL (secret-link mode).
  - ``require_login=False`` with no ``chat_token``: open access (no auth check).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import SessionLocal, get_session
from app.models import Workflow
from app.schemas import ChatPublicConfig, ChatTurnRequest, ChatTurnResponse
from app.security import current_user
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
    # Public chat is reached by external visitors with no org identity; the
    # workflow's own ``public_access`` flag is the gate. Cross-org lookup by
    # design — run_chat_turn -> start_run pins the run to the workflow's org.
    from app.tenancy import run_as_system

    with run_as_system():
        return await _load_graph_unscoped(workflow_id)


async def _load_graph_unscoped(workflow_id: str) -> dict | None:
    async with SessionLocal() as session:
        wf = (
            await session.scalars(
                select(Workflow)
                .options(selectinload(Workflow.versions))
                .where(Workflow.id == workflow_id)
            )
        ).first()
        if wf is None or not wf.active:
            return None
        # Public traffic is a production surface. Drafts may contain unreviewed
        # code/configuration and must never become externally executable before
        # the explicit publish step.
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
        require_login=bool(params.get("require_login", True)),
    )


@router.post("/{workflow_id}", response_model=ChatTurnResponse)
async def public_chat_turn(
    workflow_id: str,
    body: ChatTurnRequest,
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> ChatTurnResponse:
    graph = await _load_graph(workflow_id)
    if graph is None:
        raise _NOT_FOUND
    params = _chat_trigger_params(graph)
    if not params or not params.get("public_access"):
        raise _NOT_FOUND

    require_login = bool(params.get("require_login", True))
    chat_token = str(params.get("chat_token") or "")

    if not require_login:
        if chat_token:
            if token != chat_token:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid access token.",
                )
        # else: no token configured — open access
    else:
        await current_user(request, authorization=authorization, session=session)

    from app.tenancy import run_as_system

    try:
        with run_as_system():
            result = await run_chat_turn(
                workflow_id, body.message, body.session_id, prefer_draft=False
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
