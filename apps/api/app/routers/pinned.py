"""Pinned outputs — freeze a node's result so iterative test runs use it."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import PinnedData, Workflow
from app.schemas import PinnedItem, PinPayload
from app.security import require_permission

router = APIRouter(tags=["pinned"])


async def _require_workflow(session: AsyncSession, workflow_id: str) -> None:
    if await session.get(Workflow, workflow_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")


@router.get(
    "/workflows/{workflow_id}/pinned", response_model=list[PinnedItem]
)
async def list_pinned(
    workflow_id: str, session: AsyncSession = Depends(get_session)
):
    await _require_workflow(session, workflow_id)
    result = await session.scalars(
        select(PinnedData)
        .where(PinnedData.workflow_id == workflow_id)
        .order_by(PinnedData.node_id)
    )
    return list(result.all())


@router.put(
    "/workflows/{workflow_id}/pinned/{node_id}",
    response_model=PinnedItem,
    dependencies=[Depends(require_permission("pinned:write"))],
)
async def upsert_pinned(
    workflow_id: str,
    node_id: str,
    body: PinPayload,
    session: AsyncSession = Depends(get_session),
):
    await _require_workflow(session, workflow_id)
    existing = await session.scalar(
        select(PinnedData).where(
            PinnedData.workflow_id == workflow_id,
            PinnedData.node_id == node_id,
        )
    )
    if existing is None:
        existing = PinnedData(
            workflow_id=workflow_id, node_id=node_id, payload=body.payload
        )
        session.add(existing)
    else:
        existing.payload = body.payload
    await session.commit()
    await session.refresh(existing)
    return existing


@router.delete(
    "/workflows/{workflow_id}/pinned/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("pinned:write"))],
)
async def remove_pinned(
    workflow_id: str,
    node_id: str,
    session: AsyncSession = Depends(get_session),
):
    pinned = await session.scalar(
        select(PinnedData).where(
            PinnedData.workflow_id == workflow_id,
            PinnedData.node_id == node_id,
        )
    )
    if pinned is None:
        return
    await session.delete(pinned)
    await session.commit()
