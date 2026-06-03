from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas import ChatTurnRequest, ChatTurnResponse
from app.security import require_permission
from app.services.chat_service import NoChatTriggerError, WorkflowNotFoundError, run_chat_turn

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
