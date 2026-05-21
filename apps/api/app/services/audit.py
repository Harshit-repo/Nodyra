"""Audit logging helper.

``log_audit`` adds an event to the caller's session; the caller's commit
persists it atomically with the action it describes.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent


async def log_audit(
    session: AsyncSession,
    action: str,
    target_type: str,
    target_id: str = "",
    detail: str = "",
) -> None:
    session.add(
        AuditEvent(
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )
