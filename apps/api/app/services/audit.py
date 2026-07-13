"""Audit logging helper.

``log_audit`` adds an event to the caller's session; the caller's commit
persists it atomically with the action it describes.  The same event is
also emitted to the structured JSON log stream so operators retain the
audit trail even if the primary DB is unreachable.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditEvent
from app.tenancy import DEFAULT_ORG_ID, active_org_id

_audit_logger = logging.getLogger("nodyra.audit")
_webhook_queue: asyncio.Queue[dict[str, Any]] | None = None


def audit_webhook_enabled() -> bool:
    return bool(settings.audit_webhook_url and settings.audit_webhook_secret)


def _queue() -> asyncio.Queue[dict[str, Any]]:
    global _webhook_queue
    if _webhook_queue is None:
        _webhook_queue = asyncio.Queue(maxsize=settings.audit_webhook_queue_size)
    return _webhook_queue


def audit_event_payload(event: AuditEvent) -> dict[str, Any]:
    created_at = event.created_at or datetime.now(UTC)
    return {
        "id": event.id,
        "org_id": event.org_id,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "detail": event.detail,
        "actor_id": event.actor_id,
        "actor_email": event.actor_email,
        "session_id": event.session_id,
        "actor_type": event.actor_type,
        "created_at": created_at.isoformat(),
    }


def audit_webhook_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def send_audit_webhook_batch(events: list[dict[str, Any]]) -> None:
    if not events or not audit_webhook_enabled():
        return
    body = json.dumps(
        {"type": "audit.batch", "events": events},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Nodyra-Event": "audit.batch",
        "X-Nodyra-Signature": audit_webhook_signature(
            body, settings.audit_webhook_secret
        ),
    }
    async with httpx.AsyncClient(timeout=settings.audit_webhook_timeout_seconds) as client:
        response = await client.post(settings.audit_webhook_url, content=body, headers=headers)
        response.raise_for_status()


def enqueue_audit_webhook(event: AuditEvent) -> None:
    if not audit_webhook_enabled():
        return
    try:
        _queue().put_nowait(audit_event_payload(event))
    except asyncio.QueueFull:
        _audit_logger.warning(
            "audit webhook queue full; dropping event id=%s action=%s",
            event.id,
            event.action,
        )


async def audit_webhook_loop() -> None:
    if not audit_webhook_enabled():
        return
    queue = _queue()
    while True:
        first = await queue.get()
        batch = [first]
        try:
            deadline = asyncio.get_running_loop().time() + 0.5
            while len(batch) < settings.audit_webhook_batch_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(queue.get(), timeout=remaining))
                except TimeoutError:
                    break
            try:
                await send_audit_webhook_batch(batch)
            except Exception:  # noqa: BLE001 - SIEM delivery must not break the app
                _audit_logger.exception(
                    "audit webhook delivery failed; dropped %d event(s)", len(batch)
                )
        finally:
            for _ in batch:
                queue.task_done()


async def log_audit(
    session: AsyncSession,
    action: str,
    target_type: str,
    target_id: str = "",
    detail: str = "",
    actor_id: str | None = None,
    actor_email: str | None = None,
) -> None:
    event = AuditEvent(
        id=uuid.uuid4().hex,
        org_id=active_org_id() or DEFAULT_ORG_ID,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        actor_id=actor_id,
        actor_email=actor_email,
        created_at=datetime.now(UTC),
    )
    session.add(event)
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
    enqueue_audit_webhook(event)
