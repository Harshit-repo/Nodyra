"""Human review of MCP commands. Automation credentials cannot make decisions."""

import hashlib
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import MCPCommandApproval, User
from app.security import current_user
from app.services.audit import log_audit
from app.services.mcp_approvals import approval_payload
from app.tenancy import DEFAULT_ORG_ID, active_org_id

router = APIRouter(prefix="/mcp-approvals", tags=["mcp-approvals"])


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "deny"]


async def browser_reviewer(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    if request.headers.get("authorization") or not request.cookies.get(
        settings.session_cookie_name
    ):
        raise HTTPException(
            401,
            "Sign in to Nodyra in your browser to review this action. Bearer and automation tokens cannot approve commands.",
        )
    # The global CSRF middleware enforces the cookie/header double-submit on
    # POST. This explicit authentication bypasses no optional-auth setting.
    return await current_user(request, authorization=None, session=session)


async def _load(
    session: AsyncSession, approval_id: str, user: User, request: Request
) -> MCPCommandApproval:
    row = await session.scalar(
        select(MCPCommandApproval).where(
            MCPCommandApproval.id == approval_id,
            MCPCommandApproval.actor_id == user.id,
            MCPCommandApproval.org_id == (active_org_id() or DEFAULT_ORG_ID),
        )
    )
    if row is None:
        raise HTTPException(404, "Approval request not found in your account and workspace.")
    from app.mcp.tools import McpToolError
    from app.routers.mcp import _check_permission

    try:
        await _check_permission(session, user, row.permission, request)
    except McpToolError as exc:
        raise HTTPException(403, str(exc)) from exc
    return row


@router.get("/{approval_id}")
async def get_approval(
    approval_id: str,
    request: Request,
    response: Response,
    user: User = Depends(browser_reviewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _load(session, approval_id, user, request)
    response.headers["Cache-Control"] = "no-store"
    return approval_payload(row)


@router.post("/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    body: ApprovalDecision,
    request: Request,
    response: Response,
    user: User = Depends(browser_reviewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _load(session, approval_id, user, request)
    reviewer_credential = "Bearer " + request.cookies[settings.session_cookie_name]
    if hashlib.sha256(reviewer_credential.encode()).hexdigest() == row.principal_hash:
        raise HTTPException(
            403,
            "The requesting credential cannot approve its own command. Use an MCP automation token and a separate browser sign-in for review.",
        )
    now = datetime.now(UTC)
    status = "approved" if body.decision == "approve" else "denied"
    result = await session.execute(
        update(MCPCommandApproval)
        .where(
            MCPCommandApproval.id == row.id,
            MCPCommandApproval.status == "pending",
            MCPCommandApproval.expires_at > now,
        )
        .values(status=status, reviewed_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(
            409,
            "This request expired or already has a decision. Ask the client to request a fresh review.",
        )
    await log_audit(
        session,
        f"mcp_approval_{status}",
        "mcp_command_approval",
        row.id,
        detail=f"tool={row.tool_name}; correlation_id={row.correlation_id}",
        actor_id=user.id,
        actor_email=user.email,
    )
    await session.commit()
    await session.refresh(row)
    response.headers["Cache-Control"] = "no-store"
    return approval_payload(row)
