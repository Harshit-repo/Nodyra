from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas import ChatStreamStart, ChatTurnRequest, ChatTurnResponse
from app.security import require_permission
from app.services.chat_service import (
    NoChatTriggerError,
    WorkflowNotFoundError,
    await_chat_result,
    run_chat_turn,
    start_chat_turn,
)

router = APIRouter(tags=["chat"])


@router.post(
    "/workflows/{workflow_id}/chat",
    response_model=ChatTurnResponse,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def chat_turn(workflow_id: str, body: ChatTurnRequest) -> ChatTurnResponse:
    try:
        result = await run_chat_turn(
            workflow_id, body.message, body.session_id, prefer_draft=True
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
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


@router.post(
    "/workflows/{workflow_id}/chat/stream",
    response_model=ChatStreamStart,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def chat_turn_stream(
    workflow_id: str, body: ChatTurnRequest
) -> ChatStreamStart:
    """Start a chat turn and return immediately so the caller can stream the
    run's live agent/tool events over the run WebSocket."""
    try:
        run_id, session_id = await start_chat_turn(
            workflow_id, body.message, body.session_id, prefer_draft=True
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except NoChatTriggerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return ChatStreamStart(run_id=run_id, session_id=session_id)


@router.get(
    "/workflows/{workflow_id}/chat/result/{run_id}",
    response_model=ChatTurnResponse,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def chat_turn_result(
    workflow_id: str, run_id: str, session_id: str = ""
) -> ChatTurnResponse:
    """Fetch the final reply for a streamed chat turn once its run finishes."""
    result = await await_chat_result(run_id, session_id)
    return ChatTurnResponse(
        run_id=result.run_id,
        reply=result.reply,
        session_id=result.session_id,
        status=result.status,
    )
