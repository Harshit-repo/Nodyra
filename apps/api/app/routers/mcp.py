"""MCP server endpoint (streamable-HTTP transport, stateless JSON mode).

A single JSON-RPC 2.0 message per POST; responses are plain JSON (the MCP
spec allows servers to answer with ``application/json`` instead of an SSE
stream and to operate sessionless). Batch arrays are supported per the
JSON-RPC 2.0 spec. GET/DELETE are 405 because this server never opens
server-initiated streams.

Auth mirrors the rest of the API: optional bearer session token, required
when ``settings.auth_required``. Tool-level failures come back as MCP tool
results with ``isError`` so the calling model can self-correct; transport
auth failures are HTTP 401.
"""

import base64
import binascii
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.mcp.prompts import get_prompt, list_prompts
from app.mcp.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SUPPORTED_PROTOCOL_VERSIONS,
    initialize_result,
    jsonrpc_error,
    jsonrpc_result,
    tool_result,
)
from app.mcp.resources import (
    RESOURCE_PAGE_SIZE,
    list_resource_templates,
    list_resources,
    read_resource,
)
from app.mcp.tools import (
    STATIC_TOOLS,
    McpToolError,
    call_workflow_tool,
    get_tool,
    list_workflow_tool_descriptor_page,
    validate_tool_arguments,
)
from app.models import User
from app.security import (
    _PERMISSION_MIN_ROLE,
    _REQUIRES_AUTHENTICATED,
    _role_for,
    current_user,
    role_allows,
)
from app.services.rate_limit import allow as _rate_allow
from app.tenancy import current_org_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

TOOL_PAGE_SIZE = 100
MAX_CURSOR_OFFSET = 100_000
MAX_CURSOR_LENGTH = 512


@dataclass(frozen=True)
class _ToolCursorState:
    static_offset: int = 0
    legacy_dynamic_offset: int = 0
    after_updated_at: datetime | None = None
    after_id: str | None = None


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"nodyra:{offset}".encode()).decode().rstrip("=")


def _cursor_offset(value: object) -> int:
    if value in (None, ""):
        return 0
    if not isinstance(value, str):
        raise ValueError("cursor must be a string")
    if len(value) > MAX_CURSOR_LENGTH:
        raise ValueError("Invalid pagination cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
        prefix, raw = decoded.split(":", 1)
        offset = int(raw)
        if prefix != "nodyra" or not 0 <= offset <= MAX_CURSOR_OFFSET:
            raise ValueError
        return offset
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise ValueError("Invalid pagination cursor") from exc


def _tool_cursor(
    *,
    static_offset: int,
    anchor: tuple[datetime, str] | None,
) -> str:
    payload: dict[str, object] = {"v": 2, "s": static_offset}
    if anchor is not None:
        payload["a"] = [anchor[0].isoformat(), anchor[1]]
    raw = "nodyra-tools:" + json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _tool_cursor_state(value: object, *, static_count: int) -> _ToolCursorState:
    if value in (None, ""):
        return _ToolCursorState()
    if not isinstance(value, str) or len(value) > MAX_CURSOR_LENGTH:
        raise ValueError("Invalid pagination cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
        if not decoded.startswith("nodyra-tools:"):
            # Existing clients may still hold the v1 combined offset cursor.
            offset = _cursor_offset(value)
            return _ToolCursorState(
                static_offset=min(offset, static_count),
                legacy_dynamic_offset=max(0, offset - static_count),
            )
        payload = json.loads(decoded.removeprefix("nodyra-tools:"))
        if not isinstance(payload, dict) or payload.get("v") != 2:
            raise ValueError
        static_offset = payload.get("s")
        if (
            not isinstance(static_offset, int)
            or isinstance(static_offset, bool)
            or not 0 <= static_offset <= static_count
        ):
            raise ValueError
        anchor_value = payload.get("a")
        if anchor_value is None:
            return _ToolCursorState(static_offset=static_offset)
        if (
            not isinstance(anchor_value, list)
            or len(anchor_value) != 2
            or not all(isinstance(item, str) for item in anchor_value)
            or not 1 <= len(anchor_value[1]) <= 64
        ):
            raise ValueError
        anchor_time = datetime.fromisoformat(anchor_value[0])
        return _ToolCursorState(
            static_offset=static_offset,
            after_updated_at=anchor_time,
            after_id=anchor_value[1],
        )
    except (binascii.Error, json.JSONDecodeError, ValueError, UnicodeError) as exc:
        raise ValueError("Invalid pagination cursor") from exc


def _request_resource_url(request: Request) -> str:
    base = (
        settings.public_api_url.rstrip("/")
        if settings.public_api_url
        else str(request.base_url).rstrip("/")
    )
    return f"{base}/mcp"


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    allowed = {item.rstrip("/") for item in settings.cors_origin_list if item != "*"}
    allowed.add(
        f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}".rstrip("/")
    )
    if settings.public_api_url:
        parsed = urlsplit(settings.public_api_url)
        if parsed.scheme and parsed.netloc:
            allowed.add(f"{parsed.scheme}://{parsed.netloc}")
    return origin.rstrip("/") in allowed


def _auth_challenge(request: Request) -> Response:
    base = _request_resource_url(request).removesuffix("/mcp")
    metadata = f"{base}/.well-known/oauth-protected-resource/mcp"
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
    )


@router.get("/mcp")
async def mcp_get() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
async def mcp_delete() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def mcp_protected_resource_metadata(request: Request) -> dict:
    """RFC 9728 metadata for MCP authorization-server discovery."""
    payload: dict = {
        "resource": _request_resource_url(request),
        "bearer_methods_supported": ["header"],
        "scopes_supported": sorted(_PERMISSION_MIN_ROLE),
        "resource_name": "Nodyra MCP",
    }
    if settings.mcp_authorization_server_url:
        payload["authorization_servers"] = [
            settings.mcp_authorization_server_url.rstrip("/")
        ]
    return payload


async def _check_permission(
    session: AsyncSession,
    user: User | None,
    permission: str | None,
    request: Request,
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
    token_scopes = getattr(request.state, "api_token_scopes", None)
    if (
        token_scopes is not None
        and permission not in token_scopes
        and "*" not in token_scopes
    ):
        raise McpToolError(f"Automation token does not grant {permission}.")


async def _dispatch_single(
    body: dict,
    session: AsyncSession,
    user: User | None,
    request: Request,
) -> dict | None:
    """Handle one JSON-RPC message. Returns response dict or None for notifications."""
    if (
        not isinstance(body, dict)
        or body.get("jsonrpc") != "2.0"
        or not isinstance(body.get("method"), str)
        or body.get("id", 0) is None
    ):
        return jsonrpc_error(None, INVALID_REQUEST, "Expected a JSON-RPC request object.")

    method = body["method"]
    if "params" in body and not isinstance(body.get("params"), dict):
        return jsonrpc_error(body.get("id"), INVALID_PARAMS, "params must be an object.")
    params = body.get("params") or {}

    if "id" not in body:
        return None  # notification — no response

    req_id = body.get("id")

    if method == "initialize":
        return jsonrpc_result(req_id, initialize_result(params.get("protocolVersion")))
    if method == "ping":
        return jsonrpc_result(req_id, {})
    if method == "tools/list":
        static_tools = [tool.descriptor() for tool in STATIC_TOOLS]
        static_count = len(static_tools)
        try:
            cursor_state = _tool_cursor_state(
                params.get("cursor"), static_count=static_count
            )
        except ValueError as exc:
            return jsonrpc_error(req_id, INVALID_PARAMS, str(exc))
        tools = static_tools[
            cursor_state.static_offset : cursor_state.static_offset + TOOL_PAGE_SIZE
        ]
        remaining = TOOL_PAGE_SIZE - len(tools)
        next_static_offset = cursor_state.static_offset + len(tools)
        dynamic_anchor: tuple[datetime, str] | None = None
        dynamic_has_more = False
        if remaining > 0:
            dynamic_tools, dynamic_anchor, dynamic_has_more = (
                await list_workflow_tool_descriptor_page(
                    session,
                    offset=cursor_state.legacy_dynamic_offset,
                    after_updated_at=cursor_state.after_updated_at,
                    after_id=cursor_state.after_id,
                    limit=remaining,
                )
            )
            tools.extend(dynamic_tools)
        elif next_static_offset >= static_count:
            # Future releases may grow the static registry to an exact page
            # boundary. Probe without materializing a page so dynamic tools do
            # not become unreachable in that case.
            dynamic_probe, _, _ = await list_workflow_tool_descriptor_page(
                session,
                after_updated_at=cursor_state.after_updated_at,
                after_id=cursor_state.after_id,
                limit=1,
            )
            dynamic_has_more = bool(dynamic_probe)
        payload: dict = {"tools": tools}
        static_has_more = next_static_offset < static_count
        if static_has_more or dynamic_has_more:
            payload["nextCursor"] = _tool_cursor(
                static_offset=next_static_offset,
                anchor=dynamic_anchor,
            )
        return jsonrpc_result(req_id, payload)
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        try:
            tool = get_tool(name)
            if tool is not None:
                await _check_permission(session, user, tool.permission, request)
                validate_tool_arguments(tool.input_schema, arguments)
                payload = await tool.handler(session, user, arguments)
                return jsonrpc_result(req_id, tool_result(payload))
            # Dynamic per-workflow tool — running a workflow needs workflow:run.
            await _check_permission(session, user, "workflow:run", request)
            payload = await call_workflow_tool(session, user, name, arguments)
            if payload is not None:
                return jsonrpc_result(req_id, tool_result(payload))
            return jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown tool: {name}")
        except McpToolError as exc:
            return jsonrpc_result(req_id, tool_result(str(exc), is_error=True))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            return jsonrpc_result(req_id, tool_result(detail, is_error=True))
        except Exception:  # noqa: BLE001 - tool failures go to the model
            error_id = uuid.uuid4().hex[:12]
            logger.exception("mcp tool %s failed (error_id=%s)", name, error_id)
            return jsonrpc_result(
                req_id,
                tool_result(f"Internal tool error (reference {error_id}).", is_error=True),
            )
    if method == "resources/list":
        try:
            offset = _cursor_offset(params.get("cursor"))
        except ValueError as exc:
            return jsonrpc_error(req_id, INVALID_PARAMS, str(exc))
        resources, has_more = await list_resources(session, offset=offset)
        payload = {"resources": resources}
        if has_more:
            payload["nextCursor"] = _cursor(offset + RESOURCE_PAGE_SIZE)
        return jsonrpc_result(req_id, payload)
    if method == "resources/templates/list":
        if params.get("cursor") not in (None, ""):
            return jsonrpc_error(
                req_id, INVALID_PARAMS, "No additional resource-template pages."
            )
        return jsonrpc_result(
            req_id, {"resourceTemplates": list_resource_templates()}
        )
    if method == "resources/read":
        uri = str(params.get("uri") or "")
        if not uri:
            return jsonrpc_error(req_id, INVALID_REQUEST, "uri is required.")
        try:
            content = await read_resource(session, uri)
        except ValueError as exc:
            return jsonrpc_error(req_id, METHOD_NOT_FOUND, str(exc))
        return jsonrpc_result(req_id, {"contents": [content]})
    if method == "prompts/list":
        if params.get("cursor") not in (None, ""):
            return jsonrpc_error(req_id, INVALID_PARAMS, "No additional prompt pages.")
        return jsonrpc_result(req_id, {"prompts": list_prompts()})
    if method == "prompts/get":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        try:
            result = get_prompt(name, {str(k): str(v) for k, v in arguments.items()})
        except ValueError as exc:
            return jsonrpc_error(req_id, INVALID_PARAMS, str(exc))
        if result is None:
            return jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown prompt: {name!r}")
        return jsonrpc_result(req_id, result)

    return jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


@router.post("/mcp")
async def mcp_post(
    request: Request,
    authorization: str | None = Header(default=None),
    mcp_protocol_version: str | None = Header(
        default=None, alias="MCP-Protocol-Version"
    ),
    session: AsyncSession = Depends(get_session),
):
    if not _origin_allowed(request):
        return Response(status_code=403)
    if (
        mcp_protocol_version
        and mcp_protocol_version not in SUPPORTED_PROTOCOL_VERSIONS
    ):
        return Response(status_code=400)

    # --- transport-level auth ---
    user: User | None = None
    if authorization:
        try:
            user = await current_user(
                request, authorization=authorization, session=session
            )
        except HTTPException:
            return _auth_challenge(request)
    elif settings.auth_required:
        return _auth_challenge(request)

    # --- rate limiting ---
    org_id = current_org_id.get() or "default"
    actor = user.id if user else (request.client.host if request.client else "anon")
    identifier = f"{org_id}:{actor}"
    if not await _rate_allow("mcp", identifier, limit=120, window_seconds=60):
        return Response(status_code=429, headers={"Retry-After": "60"})

    # --- parse ---
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(jsonrpc_error(None, PARSE_ERROR, "Invalid JSON."))

    # Streamable HTTP carries exactly one JSON-RPC message per POST.
    if isinstance(body, list):
        return JSONResponse(
            jsonrpc_error(
                None,
                INVALID_REQUEST,
                "MCP accepts one JSON-RPC message per POST.",
            ),
            status_code=400,
        )

    # --- single ---
    if isinstance(body, dict) and "id" not in body:
        notification_result = await _dispatch_single(body, session, user, request)
        if notification_result is None:
            return Response(status_code=202)
        return JSONResponse(notification_result, status_code=400)

    result = await _dispatch_single(body, session, user, request)
    return JSONResponse(result)
