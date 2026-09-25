"""Authenticated MCP proxy and explicit operator contract management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.mcp.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SUPPORTED_PROTOCOL_VERSIONS,
    initialize_result,
    jsonrpc_error,
    jsonrpc_result,
)
from app.models import MCPConnection, MCPGatewayInvocation, User
from app.routers.mcp import _origin_allowed
from app.security import require_permission
from app.services import mcp_client, rate_limit
from app.services import mcp_gateway as gateway
from app.services.audit import log_audit
from app.services.json_responses import NodyraJSONResponse
from app.tenancy import active_org_id


def enabled() -> None:
    if not settings.mcp_gateway_enabled:
        raise HTTPException(404, "MCP gateway is disabled")


router = APIRouter(prefix="/mcp-gateway", tags=["mcp-gateway"], dependencies=[Depends(enabled)])
call_permission = require_permission("mcp_gateway:call")
manage_permission = require_permission("mcp_gateway:manage")


async def _connection(session: AsyncSession, connection_id: str) -> MCPConnection:
    row = await session.scalar(
        select(MCPConnection).where(
            MCPConnection.id == connection_id,
            MCPConnection.org_id == (active_org_id() or "default"),
        )
    )
    if row is None:
        raise HTTPException(404, "Connection not found")
    return row


@router.get("/{connection_id}/catalog")
async def discover_catalog(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    actor: User = Depends(manage_permission),
) -> dict:
    conn = await _connection(session, connection_id)
    if not await rate_limit.allow(
        "gateway_discovery", f"{conn.org_id}:{actor.id}", limit=20, fail_closed=True
    ):
        raise HTTPException(429, "Discovery rate limit or shared counter unavailable")
    conn, secret = await mcp_client._load_conn_with_secret(conn.id, conn.org_id, session)
    try:
        catalog = await mcp_client.discover_catalog(conn, decrypted_secret=secret)
    except mcp_client.MCPError:
        raise HTTPException(502, "Tool discovery failed") from None
    return {**catalog, "catalog_digest": gateway.catalog_digest(conn, catalog)}


class PolicyApproval(BaseModel):
    catalog_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    # Values are optional extra JSON Schema restrictions, e.g. channel const.
    tools: dict[str, dict] = Field(min_length=1, max_length=100)
    workflow_version_ids: list[str] = Field(default_factory=list, max_length=100)


@router.put("/{connection_id}/policy")
async def approve_policy(
    connection_id: str,
    body: PolicyApproval,
    session: AsyncSession = Depends(get_session),
    actor: User = Depends(manage_permission),
) -> dict:
    conn = await _connection(session, connection_id)
    try:
        policy_bytes = gateway.canonical_bytes(body.model_dump())
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Policy must contain finite JSON values") from None
    if len(policy_bytes) > settings.mcp_gateway_max_arguments_bytes:
        raise HTTPException(413, "Policy too large")
    try:
        policy = await gateway.approve_policy(
            session,
            conn,
            actor_id=actor.id,
            tools=body.tools,
            workflow_version_ids=body.workflow_version_ids,
            expected_catalog_digest=body.catalog_digest,
        )
    except ValueError:
        raise HTTPException(
            409,
            "Policy approval failed: review the current catalog, selected tools, constraints, and workflow versions",
        ) from None
    except mcp_client.MCPError:
        raise HTTPException(502, "Tool discovery failed; policy unchanged") from None
    await log_audit(
        session,
        "approve_contract",
        "mcp_gateway",
        conn.id,
        f"revision={policy['revision']} tools={len(body.tools)}",
        actor_id=actor.id,
        actor_email=actor.email,
    )
    await session.commit()
    return {
        "revision": policy["revision"],
        "approved_by": policy["approved_by"],
        "tools": list(policy["tools"]),
        "workflow_version_ids": list(policy["workflow_versions"]),
    }


@router.get("/{connection_id}/policy")
async def get_policy(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    actor: User = Depends(manage_permission),
) -> dict:
    conn = await _connection(session, connection_id)
    policy = conn.gateway_policy
    return {
        "policy": policy,
        "valid_for_connection": bool(
            policy and policy.get("connection_fingerprint") == gateway.connection_fingerprint(conn)
        ),
    }


@router.delete("/{connection_id}/policy", status_code=204)
async def revoke_policy(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    actor: User = Depends(manage_permission),
) -> None:
    conn = await _connection(session, connection_id)
    conn.gateway_policy = None
    await log_audit(
        session,
        "revoke_contract",
        "mcp_gateway",
        conn.id,
        actor_id=actor.id,
        actor_email=actor.email,
    )
    await session.commit()


@router.get("/{connection_id}/invocations")
async def list_invocations(
    connection_id: str,
    limit: int = 25,
    session: AsyncSession = Depends(get_session),
    actor: User = Depends(manage_permission),
) -> list[dict]:
    # Evidence remains readable after deleting the connection.
    rows = await session.scalars(
        select(MCPGatewayInvocation)
        .where(
            MCPGatewayInvocation.connection_id == connection_id,
            MCPGatewayInvocation.org_id == (active_org_id() or "default"),
        )
        .order_by(MCPGatewayInvocation.created_at.desc(), MCPGatewayInvocation.id.desc())
        .limit(max(1, min(limit, 100)))
    )
    return [gateway.invocation_info(row) for row in rows]


@router.get("/{connection_id}", operation_id="mcpGatewayUnsupportedStream")
@router.delete("/{connection_id}", operation_id="mcpGatewayUnsupportedSessionDeletion")
async def unsupported_stream(
    connection_id: str, request: Request, actor: User = Depends(call_permission)
) -> Response:
    if not _origin_allowed(request):
        raise HTTPException(403, "Origin not allowed")
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/{connection_id}")
async def proxy(
    connection_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: User = Depends(call_permission),
) -> Response:
    if not _origin_allowed(request):
        raise HTTPException(403, "Origin not allowed")
    protocol = request.headers.get("mcp-protocol-version")
    if protocol and protocol not in SUPPORTED_PROTOCOL_VERSIONS:
        raise HTTPException(400, "Unsupported MCP protocol version")
    org_id = active_org_id() or "default"
    if not await rate_limit.allow(
        "gateway_requests",
        f"{org_id}:{actor.id}",
        limit=settings.mcp_gateway_rate_limit_per_minute * 4,
        fail_closed=True,
    ):
        return Response(status_code=429, headers={"Retry-After": "60"})
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > settings.mcp_gateway_max_arguments_bytes + 4096:
            raise HTTPException(413, "MCP request too large")
    try:
        body = mcp_client._decode_json(raw)
    except mcp_client.MCPError:
        return JSONResponse(jsonrpc_error(None, PARSE_ERROR, "Invalid JSON"), status_code=400)
    if (
        not isinstance(body, dict)
        or body.get("jsonrpc") != "2.0"
        or not isinstance(body.get("method"), str)
    ):
        return JSONResponse(
            jsonrpc_error(None, INVALID_REQUEST, "Expected one JSON-RPC message"), status_code=400
        )
    req_id = body.get("id")
    if "id" not in body:
        return Response(status_code=202)  # Stateless endpoint accepts notifications.
    if type(req_id) not in (str, int):
        return JSONResponse(
            jsonrpc_error(None, INVALID_REQUEST, "Invalid request ID"), status_code=400
        )
    params = body.get("params", {})
    if not isinstance(params, dict):
        return JSONResponse(jsonrpc_error(req_id, INVALID_PARAMS, "params must be an object"))
    method = body["method"]
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return JSONResponse(jsonrpc_error(req_id, INVALID_PARAMS, "Tool name is required"))
        try:
            result, correlation = await gateway.execute(
                session,
                connection_id,
                name,
                params.get("arguments", {}),
                principal=gateway.Principal(org_id, actor.id),
                return_envelope=True,
            )
            result = {
                **result,
                "_meta": {
                    **result.get("_meta", {}),
                    "io.nodyra/correlationId": correlation,
                    "io.nodyra/outcome": "provider_reported_success",
                },
            }
        except gateway.GatewayDenied as exc:
            result = {
                "isError": True,
                "content": [{"type": "text", "text": str(exc)}],
                "_meta": {
                    "io.nodyra/correlationId": exc.invocation_id,
                    "io.nodyra/reason": exc.reason,
                },
            }
        return NodyraJSONResponse(
            jsonrpc_result(req_id, result), headers={"Cache-Control": "no-store"}
        )
    conn = await _connection(session, connection_id)
    if method == "initialize":
        result = initialize_result(params.get("protocolVersion"))
        result["serverInfo"] = {**result["serverInfo"], "name": "nodyra-mcp-gateway"}
        result["capabilities"] = {"tools": {"listChanged": False}}
        result["instructions"] = (
            "Only operator-approved tools are exposed. Read tool errors and correlation IDs; a provider success does not independently confirm an external write."
        )
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        if params.get("cursor"):
            return JSONResponse(jsonrpc_error(req_id, INVALID_PARAMS, "Unknown pagination cursor"))
        policy = conn.gateway_policy
        valid = (
            conn.enabled
            and isinstance(policy, dict)
            and policy.get("connection_fingerprint") == gateway.connection_fingerprint(conn)
        )
        result = (
            {
                "tools": [
                    entry["manifest"]
                    for name, entry in policy["tools"].items()
                    if conn.allowed_tools is None or name in conn.allowed_tools
                ]
            }
            if valid
            else {"tools": []}
        )
    else:
        return JSONResponse(
            jsonrpc_error(req_id, METHOD_NOT_FOUND, "Method not supported by gateway")
        )
    return NodyraJSONResponse(jsonrpc_result(req_id, result), headers={"Cache-Control": "no-store"})
