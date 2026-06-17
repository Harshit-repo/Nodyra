"""MCP server endpoint (streamable-HTTP transport, stateless JSON mode).

A single JSON-RPC 2.0 message per POST; responses are plain JSON (the MCP
spec allows servers to answer with ``application/json`` instead of an SSE
stream and to operate sessionless). GET/DELETE are 405 because this server
never opens server-initiated streams.

Auth mirrors the rest of the API: optional bearer session token, required
when ``settings.auth_required``. Tool-level failures come back as MCP tool
results with ``isError`` so the calling model can self-correct; transport
auth failures are HTTP 401.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    initialize_result,
    jsonrpc_error,
    jsonrpc_result,
    tool_result,
)
from app.mcp.tools import (
    STATIC_TOOLS,
    McpToolError,
    call_workflow_tool,
    get_tool,
    list_workflow_tool_descriptors,
)
from app.models import User
from app.security import (
    _PERMISSION_MIN_ROLE,
    _REQUIRES_AUTHENTICATED,
    _role_for,
    current_user,
    role_allows,
)
from app.tenancy import current_org_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])


@router.get("/mcp")
async def mcp_get() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
async def mcp_delete() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


async def _check_permission(
    session: AsyncSession, user: User | None, permission: str | None
) -> None:
    """RBAC for one tool call. Raises McpToolError on denial."""
    if permission is None:
        return
    minimum = _PERMISSION_MIN_ROLE[permission]
    if user is None:
        # Mirror require_permission: some permissions demand an authenticated
        # actor even when auth_required is globally off (see security.py).
        if settings.auth_required or permission in _REQUIRES_AUTHENTICATED:
            raise McpToolError("Authentication required for this tool.")
        return
    org_id = current_org_id.get() if settings.multi_tenancy_enabled else None
    role = await _role_for(session, user, org_id)
    if not role_allows(role, minimum):
        raise McpToolError(f"This tool requires the {minimum} role or higher.")


@router.post("/mcp")
async def mcp_post(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    # --- transport-level auth ---
    user: User | None = None
    if authorization:
        try:
            user = await current_user(
                request, authorization=authorization, session=session
            )
        except HTTPException:
            return Response(
                status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
    elif settings.auth_required:
        return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})

    # --- parse ---
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(jsonrpc_error(None, PARSE_ERROR, "Invalid JSON."))
    if isinstance(body, list):
        return JSONResponse(
            jsonrpc_error(None, INVALID_REQUEST, "Batch requests are not supported.")
        )
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        return JSONResponse(
            jsonrpc_error(None, INVALID_REQUEST, "Expected a JSON-RPC request object.")
        )

    method = body["method"]
    params = body.get("params") if isinstance(body.get("params"), dict) else {}

    # Notifications (no id) are acknowledged and ignored.
    if "id" not in body:
        return Response(status_code=202)
    req_id = body.get("id")

    if method == "initialize":
        return JSONResponse(
            jsonrpc_result(req_id, initialize_result(params.get("protocolVersion")))
        )
    if method == "ping":
        return JSONResponse(jsonrpc_result(req_id, {}))
    if method == "tools/list":
        tools = [tool.descriptor() for tool in STATIC_TOOLS]
        tools.extend(await list_workflow_tool_descriptors(session))
        return JSONResponse(jsonrpc_result(req_id, {"tools": tools}))
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        try:
            tool = get_tool(name)
            if tool is not None:
                await _check_permission(session, user, tool.permission)
                payload = await tool.handler(session, user, arguments)
                return JSONResponse(jsonrpc_result(req_id, tool_result(payload)))
            # Dynamic per-workflow tool — running a workflow needs workflow:run.
            await _check_permission(session, user, "workflow:run")
            payload = await call_workflow_tool(session, user, name, arguments)
            if payload is not None:
                return JSONResponse(jsonrpc_result(req_id, tool_result(payload)))
            return JSONResponse(
                jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown tool: {name}")
            )
        except McpToolError as exc:
            return JSONResponse(
                jsonrpc_result(req_id, tool_result(str(exc), is_error=True))
            )
        except Exception as exc:  # noqa: BLE001 - tool failures go to the model
            logger.exception("mcp tool %s failed", name)
            return JSONResponse(
                jsonrpc_result(
                    req_id,
                    tool_result(f"{type(exc).__name__}: {exc}", is_error=True),
                )
            )

    return JSONResponse(
        jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown method: {method}")
    )
