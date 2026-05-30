from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuditEvent
from app.schemas import AuditEventInfo, PageResponse
from app.security import require_permission

router = APIRouter(tags=["audit"])


@router.get(
    "/audit",
    response_model=PageResponse[AuditEventInfo],
    dependencies=[Depends(require_permission("audit:read"))],
)
async def list_audit_events(
    actor_id: str | None = Query(default=None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc())
    if actor_id is not None:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    count_stmt = select(func.count()).select_from(AuditEvent)
    if actor_id is not None:
        count_stmt = count_stmt.where(AuditEvent.actor_id == actor_id)
    total = await session.scalar(count_stmt)
    result = await session.scalars(stmt.offset(offset).limit(limit))
    return PageResponse(items=list(result.all()), total=total or 0, limit=limit, offset=offset)
