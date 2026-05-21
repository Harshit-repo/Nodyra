from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuditEvent
from app.schemas import AuditEventInfo

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=list[AuditEventInfo])
async def list_audit_events(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(
        select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100)
    )
    return list(result.all())
