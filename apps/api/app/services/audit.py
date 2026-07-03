"""Audit logging helper.

``log_audit`` adds an event to the caller's session; the caller's commit
persists it atomically with the action it describes.  The same event is
also emitted to the structured JSON log stream so operators retain the
audit trail even if the primary DB is unreachable.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent

_audit_logger = logging.getLogger("nodyra.audit")


async def log_audit(
    session: AsyncSession,
    action: str,
    target_type: str,
    target_id: str = "",
    detail: str = "",
    actor_id: str | None = None,
    actor_email: str | None = None,
) -> None:
    session.add(
        AuditEvent(
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            actor_id=actor_id,
            actor_email=actor_email,
        )
    )
    # Mirror to the structured JSON log stream so the audit trail is available
    # even when the DB is down.  Use ``extra`` so the log formatter injects
    # request_id / org_id / trace_id for correlation.
    _audit_logger.info(
        "audit: %s %s %s",
        action,
        target_type,
        target_id,
        extra={
            "audit_action": action,
            "audit_target_type": target_type,
            "audit_target_id": target_id,
            "audit_detail": detail,
            "audit_actor_id": actor_id or "",
            "audit_actor_email": actor_email or "",
        },
    )
