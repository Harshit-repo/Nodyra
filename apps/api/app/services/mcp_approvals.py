"""Server-owned, one-use approval grants for inbound MCP mutations.

The agent can request a grant, but only a browser session can approve it.
No bearer token or caller-supplied approval boolean constitutes a decision.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Deployment, Environment, MCPCommandApproval, Run, User, Workflow
from app.services.audit import log_audit
from app.services.execution_actor import RunAdmissionGuard
from app.services.redaction import load_secret_values_for_org, redact_value
from app.tenancy import DEFAULT_ORG_ID, active_org_id
from nodyra import __version__ as NODYRA_VERSION

approved_command: ContextVar[str | None] = ContextVar("approved_mcp_command", default=None)
APPROVAL_TTL = timedelta(minutes=15)
MAX_ARGUMENT_BYTES = 1_000_000
MAX_PENDING_APPROVALS = 25
RUN_COMMANDS = frozenset({"run_workflow", "retry_run"})


class ApprovalError(ValueError):
    pass


class ApprovalRequired(ApprovalError):
    def __init__(self, approval: MCPCommandApproval):
        self.payload = {
            "error": "human_approval_required",
            "message": "Review this exact action in Nodyra, then retry with its approval_id. approved_by_user is not authorization.",
            "approval_id": approval.id,
            "review_path": f"/mcp-approvals/{approval.id}",
            "org_id": approval.org_id,
            "correlation_id": approval.correlation_id,
            "expires_at": _utc(approval.expires_at).isoformat(),
        }
        super().__init__(self.payload["message"])


def command_arguments(arguments: dict) -> dict:
    return {
        key: value
        for key, value in arguments.items()
        if key not in {"approval_id", "approved_by_user"}
    }


def argument_digest(arguments: dict) -> str:
    try:
        encoded = json.dumps(
            command_arguments(arguments),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ApprovalError("Tool arguments must be finite JSON values.") from exc
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ApprovalError("Tool arguments exceed the approval review size limit.")
    # Key the digest so a leaked approval record cannot be used to guess a
    # low-entropy secret from otherwise known arguments.
    return hmac.new(settings.secret_key.encode(), encoded, hashlib.sha256).hexdigest()


def _principal_hash(request: Request) -> str:
    # Binding a grant to the requesting credential also prevents another PAT
    # owned by the same user from taking over an approved command.
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise ApprovalError("An authenticated MCP bearer identity is required for this action.")
    return hashlib.sha256(authorization.encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _state_digest(row: Any) -> str:
    # Timestamp precision differs across databases. Hash configuration too,
    # so two edits in the same second still invalidate an earlier decision.
    operational = {
        "created_at",
        "updated_at",
        "last_fired",
        "status",
        "status_detail",
        "worker_rss_estimate_bytes",
        "github_sync_sha",
        "github_sync_status",
        "github_sync_conflict_sha",
    }
    state = {
        column.key: getattr(row, column.key)
        for column in row.__table__.columns
        if column.key not in operational
    }
    return hmac.new(
        settings.secret_key.encode(),
        json.dumps(
            state, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
        ).encode(),
        hashlib.sha256,
    ).hexdigest()


async def target_snapshot(session: AsyncSession, arguments: dict, *, lock: bool = False) -> dict:
    """Resolve the actual target; caller-supplied revision claims are not trusted."""
    snapshot: dict[str, Any] = {}
    workflow_id = arguments.get("workflow_id")
    for field, model in (
        ("schedule_id", Deployment),
        ("run_id", Run),
        ("environment_id", Environment),
    ):
        value = arguments.get(field)
        if not value:
            continue
        stmt = select(model).where(model.id == str(value)).execution_options(populate_existing=True)
        if lock:
            stmt = stmt.with_for_update(key_share=True)
        row = await session.scalar(stmt)
        if row is None:
            raise ApprovalError(f"{field.removesuffix('_id').title()} not found: {value}")
        snapshot[field] = str(value)
        if field in {"schedule_id", "run_id"}:
            workflow_id = row.workflow_id
        if field != "run_id":
            snapshot[field + "_updated_at"] = _utc(row.updated_at).isoformat()
            snapshot[field + "_state_digest"] = _state_digest(row)
    if workflow_id:
        stmt = (
            select(Workflow)
            .where(Workflow.id == str(workflow_id))
            .execution_options(populate_existing=True)
        )
        if lock:
            # FOR NO KEY UPDATE excludes configuration edits while allowing
            # other transactions to reference this row via a foreign key.
            stmt = stmt.with_for_update(key_share=True)
        workflow = await session.scalar(stmt)
        if workflow is None:
            raise ApprovalError(f"Workflow not found: {workflow_id}")
        snapshot.update(
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            graph_revision=int(workflow.graph_revision or 0),
            published_version=workflow.published_version,
            workflow_updated_at=_utc(workflow.updated_at).isoformat(),
            workflow_state_digest=_state_digest(workflow),
        )
    return snapshot


async def authorize_command(
    session: AsyncSession,
    user: User | None,
    request: Request,
    *,
    tool_name: str,
    permission: str,
    arguments: dict,
    input_schema: dict,
) -> MCPCommandApproval:
    if user is None:
        raise ApprovalError(
            "This action requires explicit human approval from a signed-in Nodyra user, including in development mode."
        )
    principal = _principal_hash(request)
    digest = argument_digest(arguments)
    org_id = active_org_id() or DEFAULT_ORG_ID
    snapshot = await target_snapshot(session, arguments)
    contract_digest = argument_digest(
        {
            "input_schema": input_schema,
            "nodyra_version": NODYRA_VERSION,
        }
    )
    snapshot["tool_contract_digest"] = contract_digest
    approval_id = arguments.get("approval_id")
    now = datetime.now(UTC)
    if not approval_id:
        # Serializing per actor closes the concurrent-request quota race on
        # PostgreSQL. SQLite still fails closed on concurrent write conflicts.
        await session.execute(select(User.id).where(User.id == user.id).with_for_update())
        pending = (
            await session.scalars(
                select(MCPCommandApproval)
                .where(
                    MCPCommandApproval.org_id == org_id,
                    MCPCommandApproval.actor_id == user.id,
                    MCPCommandApproval.status == "pending",
                    MCPCommandApproval.expires_at > now,
                )
                .limit(MAX_PENDING_APPROVALS)
            )
        ).all()
        for existing in pending:
            if (
                existing.principal_hash == principal
                and existing.tool_name == tool_name
                and existing.permission == permission
                and existing.arguments_digest == digest
                and existing.target_snapshot == snapshot
            ):
                await session.commit()
                raise ApprovalRequired(existing)
        if len(pending) >= MAX_PENDING_APPROVALS:
            raise ApprovalError(
                "Too many pending approval requests. Review existing requests or wait for them to expire before requesting more."
            )
        secrets = await load_secret_values_for_org(org_id, session)
        approval = MCPCommandApproval(
            id=uuid.uuid4().hex,
            org_id=org_id,
            actor_id=user.id,
            principal_hash=principal,
            tool_name=tool_name,
            permission=permission,
            arguments_digest=digest,
            arguments_preview=redact_value(command_arguments(arguments), secrets),
            target_snapshot=snapshot,
            correlation_id=uuid.uuid4().hex,
            status="pending",
            expires_at=now + APPROVAL_TTL,
        )
        session.add(approval)
        await log_audit(
            session,
            "mcp_approval_requested",
            "mcp_command_approval",
            approval.id,
            detail=json.dumps(
                {
                    "tool": tool_name,
                    "arguments_digest": digest,
                    "correlation_id": approval.correlation_id,
                }
            ),
            actor_id=user.id,
            actor_email=user.email,
        )
        await session.commit()
        raise ApprovalRequired(approval)
    approval = await session.scalar(
        select(MCPCommandApproval).where(
            MCPCommandApproval.id == approval_id,
            MCPCommandApproval.org_id == org_id,
            MCPCommandApproval.actor_id == user.id,
        )
    )
    if approval is None:
        raise ApprovalError("Approval not found for this actor and organization.")
    if (
        approval.principal_hash != principal
        or approval.tool_name != tool_name
        or approval.permission != permission
        or approval.arguments_digest != digest
    ):
        raise ApprovalError(
            "Approval does not match this credential, tool, or exact arguments. Request a new review."
        )
    if approval.target_snapshot != snapshot:
        raise ApprovalError(
            "The workflow or target changed after this approval was requested. Request a new review."
        )
    if _utc(approval.expires_at) <= now:
        raise ApprovalError("Approval expired. Request a new review.")
    consumed = await session.execute(
        update(MCPCommandApproval)
        .where(
            MCPCommandApproval.id == approval.id,
            MCPCommandApproval.status == "approved",
            MCPCommandApproval.expires_at > now,
        )
        .values(status="consumed", consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        raise ApprovalError("Approval has not been granted, was denied, or has already been used.")
    await log_audit(
        session,
        "mcp_approval_consumed",
        "mcp_command_approval",
        approval.id,
        detail=json.dumps({"tool": tool_name, "correlation_id": approval.correlation_id}),
        actor_id=user.id,
        actor_email=user.email,
    )
    # Burn the grant even if execution later fails. A failed/unknown effect
    # must not be blindly replayable by reusing the same approval.
    await session.commit()
    current_snapshot = await target_snapshot(session, arguments, lock=True)
    current_snapshot["tool_contract_digest"] = contract_digest
    if current_snapshot != snapshot:
        raise ApprovalError(
            "The workflow or target changed before execution. Request a new review."
        )
    return approval


def run_admission_guard(approval: MCPCommandApproval, arguments: dict) -> RunAdmissionGuard:
    """Carry exact approval binding across the request-to-runner boundary."""
    expected = dict(approval.target_snapshot)
    expected.pop("tool_contract_digest", None)
    target_arguments = {
        key: value for key, value in arguments.items()
        if key in {"workflow_id", "run_id", "schedule_id", "environment_id"}
    }
    used = False

    async def guard(session: AsyncSession, workflow_id: str) -> None:
        nonlocal used
        if used:
            raise ApprovalError("Run admission approval has already been used.")
        used = True
        if workflow_id != expected.get("workflow_id"):
            raise ApprovalError("Approval does not match the workflow being admitted.")
        current = await target_snapshot(session, target_arguments, lock=True)
        if current != expected:
            raise ApprovalError(
                "The workflow or target changed before run admission. Request a new review."
            )

    return guard


def approval_payload(approval: MCPCommandApproval) -> dict:
    expired = _utc(approval.expires_at) <= datetime.now(UTC)
    return {
        "id": approval.id,
        "tool_name": approval.tool_name,
        "status": "expired"
        if expired and approval.status in {"pending", "approved"}
        else approval.status,
        "arguments": approval.arguments_preview,
        "arguments_digest": approval.arguments_digest,
        "target": approval.target_snapshot,
        "actor_id": approval.actor_id,
        "org_id": approval.org_id,
        "correlation_id": approval.correlation_id,
        "expires_at": _utc(approval.expires_at).isoformat(),
        "created_at": _utc(approval.created_at).isoformat(),
    }
