"""Trusted execution identity captured at authentication, then persisted on runs.

The context is request-local. Workers never supply this identity: their MCP
calls resolve the initiator from the durable Run row instead.
"""

import hashlib
import hmac
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True)
class ExecutionActor:
    id: str | None = None
    kind: str = "system"


SYSTEM_ACTOR = ExecutionActor()
current_execution_actor: ContextVar[ExecutionActor] = ContextVar(
    "current_execution_actor", default=SYSTEM_ACTOR
)


@dataclass(frozen=True)
class ExecutionAttempt:
    """Host-owned root lease; it never crosses the worker protocol."""

    run_id: str
    lease_token: str


current_execution_attempt: ContextVar[ExecutionAttempt | None] = ContextVar(
    "current_execution_attempt", default=None
)


async def validate_execution_attempt(
    session: AsyncSession, *, run_id: str,
) -> str | None:
    """Return a denial reason if the active host no longer owns execution.

    Child runs have no queue ledger. Walk their durable ancestry to validate
    the root lease they inherit. Column queries deliberately read fresh state
    instead of accepting a stale Run/queue object from the identity map.
    """
    from app.models import Run, RunQueueEntry

    attempt = current_execution_attempt.get()
    current_id = run_id
    org_id = None
    seen: set[str] = set()
    for _ in range(128):
        if current_id in seen:
            return "execution_attempt_invalid_ancestry"
        seen.add(current_id)
        run = (await session.execute(select(
            Run.id, Run.parent_run_id, Run.org_id, Run.status,
        ).where(Run.id == current_id))).one_or_none()
        if run is None:
            return "execution_attempt_run_unavailable"
        if org_id is None:
            org_id = run.org_id
        elif run.org_id != org_id:
            return "execution_attempt_invalid_ancestry"
        if run.status != "running":
            return "execution_attempt_run_not_running"
        entry = (await session.execute(select(
            RunQueueEntry.status, RunQueueEntry.lease_token, RunQueueEntry.lease_expires_at,
        ).where(RunQueueEntry.run_id == current_id))).one_or_none()
        if entry is not None:
            if attempt is None:
                return "execution_attempt_missing"
            if attempt.run_id != current_id:
                return "execution_attempt_mismatch"
            if entry.status != "running":
                return "execution_attempt_not_running"
            if not entry.lease_token or not hmac.compare_digest(entry.lease_token, attempt.lease_token):
                return "execution_attempt_lease_changed"
            expires_at = entry.lease_expires_at
            if expires_at is None:
                return "execution_attempt_lease_expired"
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                return "execution_attempt_lease_expired"
            return None
        if attempt is not None and attempt.run_id == current_id:
            return None  # Direct host execution without a durable queue ledger.
        if run.parent_run_id is None:
            return None if attempt is None else "execution_attempt_mismatch"
        current_id = run.parent_run_id
    return "execution_attempt_invalid_ancestry"


def execution_graph_digest(graph: dict) -> str:
    """Bind a run to its original graph, before resolving credential values."""
    encoded = json.dumps(
        graph, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bind_authenticated_actor(user_id: str) -> None:
    """Bind only an identity already verified by the authentication layer."""
    current_execution_actor.set(ExecutionActor(id=user_id, kind="user"))


@contextmanager
def system_execution_actor():
    """Attribute a verified server-owned trigger to its automation principal.

    Only trigger adapters use this after their normal ingress authorization.
    Request fields such as mode never select this identity.
    """
    token = current_execution_actor.set(SYSTEM_ACTOR)
    try:
        yield
    finally:
        current_execution_actor.reset(token)


class ExecutionActorMiddleware:
    """Reset request context even on errors and preserve outer task identity."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        token = current_execution_actor.set(ExecutionActor(kind="anonymous"))
        attempt_token = current_execution_attempt.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            current_execution_attempt.reset(attempt_token)
            current_execution_actor.reset(token)
