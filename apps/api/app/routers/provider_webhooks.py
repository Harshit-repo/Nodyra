"""Provider-managed webhook + WebSocket trigger ingress."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.config import settings
from app.db import SessionLocal
from app.models import ProviderTriggerSubscription, Workflow, WorkflowVersion
from app.security import get_client_ip
from app.services.provider_triggers import (
    _node_params_by_id,
    _resolved_params,
    dispatch_provider_webhook,
)
from nodyra_nodes.integrations_v2.specs import ProviderTriggerRequest

router = APIRouter(tags=["provider-webhooks"])

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


_PROVIDER_WEBHOOK_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB


async def _provider_request(request: Request) -> ProviderTriggerRequest:
    # Check Content-Length before consuming body (P1-16).
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _PROVIDER_WEBHOOK_MAX_BODY_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "Request body too large",
                )
        except (ValueError, TypeError):
            pass
    raw = await request.body()
    if len(raw) > _PROVIDER_WEBHOOK_MAX_BODY_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Request body too large",
        )
    body: object
    try:
        body = json.loads(raw) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = raw.decode("utf-8", "replace")
    return ProviderTriggerRequest(
        headers=dict(request.headers),
        query=dict(request.query_params),
        body=body,
        raw_body=raw,
    )


async def _enforce_provider_webhook_rate_limit(subscription_id: str, request: Request) -> None:
    """Rate-limit provider webhook ingress per (subscription, caller IP).

    Provider callbacks are unauthenticated at the Nodyra layer (auth is the
    provider's webhook signature).  Without a rate limit a flood of validly
    signed deliveries could overwhelm the run queue.
    """
    if not settings.webhook_rate_limit_enabled:
        return
    from app.services import rate_limit

    ip = get_client_ip(request)
    allowed = await rate_limit.allow(
        "provider_webhook",
        f"{subscription_id}:{ip}",
        limit=settings.webhook_rate_limit_per_minute,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many provider webhook requests; slow down.",
        )


async def provider_webhook(subscription_id: str, request: Request) -> Response:
    """Receive a delivery for a lifecycle-managed provider trigger."""
    from app.tenancy import run_as_system

    # OPTIONS returns 204 for CORS preflight.
    if request.method == "OPTIONS":
        return Response(status_code=204)

    await _enforce_provider_webhook_rate_limit(subscription_id, request)

    # Cross-org by design: the subscription decides which org's workflow
    # fires; the external provider has no Nodyra identity. start_run pins
    # the resulting run to its workflow's org.
    try:
        with run_as_system():
            result = await dispatch_provider_webhook(
                subscription_id,
                await _provider_request(request),
            )
    except KeyError as exc:
        raise HTTPException(404, "Provider trigger subscription not found") from exc
    body = result.body
    if body is None:
        body = {
            "message": "Provider event acknowledged",
            "received_at": datetime.now(UTC).isoformat(),
            "runs": result.run_ids,
        }
    if isinstance(body, (dict, list)):
        return JSONResponse(
            content=body,
            status_code=result.status,
            headers=result.headers,
        )
    return Response(
        content=str(body),
        status_code=result.status,
        headers=result.headers,
        media_type="text/plain",
    )


for _method in _METHODS:
    router.add_api_route(
        "/provider-webhook/{subscription_id}",
        provider_webhook,
        methods=[_method],
        operation_id=f"provider_webhook_{_method.lower()}",
    )


_WS_AUTH_HEADER_MAX = 512


def _ws_client_token(websocket: WebSocket) -> str:
    query_token = websocket.query_params.get("token", "")
    if query_token:
        return query_token
    raw = (websocket.headers.get("authorization") or "")[:_WS_AUTH_HEADER_MAX]
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def _ws_auth_expected(resolved_params: dict) -> str:
    value = resolved_params.get("auth_token")
    if isinstance(value, dict):
        value = value.get("token")
    return str(value or "")


async def _ws_subscriptions_for_path(path: str) -> list[tuple[dict, str]]:
    """(resolved_params, subscription_id) for every active websocket_trigger
    subscription whose ``path`` param matches the requested URL path.

    Cross-org by design (like provider webhooks): the subscription decides
    which org's workflow fires.
    """
    from app.tenancy import run_as_system

    with run_as_system():
        async with SessionLocal() as session:
            rows = (
                await session.scalars(
                    select(ProviderTriggerSubscription).where(
                        ProviderTriggerSubscription.status == "active",
                        ProviderTriggerSubscription.node_type == "websocket_trigger",
                    )
                )
            ).all()
            matches: list[tuple[dict, str]] = []
            for row in rows:
                workflow = await session.get(Workflow, row.workflow_id)
                if workflow is None:
                    continue
                version: WorkflowVersion | None = None
                if row.workflow_version_id:
                    version = await session.get(WorkflowVersion, row.workflow_version_id)
                if version is None:
                    continue
                graph = version.graph or {"nodes": [], "edges": []}
                params = _node_params_by_id(graph).get(row.node_id, {})
                resolved = await _resolved_params(
                    session,
                    workflow_id=workflow.id,
                    environment_id=workflow.environment_id,
                    params=params,
                )
                resolved = resolved if isinstance(resolved, dict) else {}
                if str(resolved.get("path") or "ws").strip("/") != path:
                    continue
                matches.append((resolved, row.id))
    return matches


@router.websocket("/ws/triggers/{path}")
async def websocket_trigger_ingress(websocket: WebSocket, path: str) -> None:
    """Inbound WebSocket messages for websocket_trigger subscriptions.

    One connection can serve every subscription that shares a ``path``; each
    text message is dispatched to all of them. Auth per the subscription's
    ``auth_type`` param: token/header require a matching token in the
    ``?token=`` query param or the ``Authorization`` header.
    """
    await websocket.accept()
    try:
        matches = await _ws_subscriptions_for_path(path.strip("/"))
        if not matches:
            await websocket.send_json({"error": f"no active websocket trigger at path '{path}'"})
            await websocket.close(code=1008)
            return

        protected = [m for m in matches if str(m[0].get("auth_type") or "none") != "none"]
        if protected:
            provided = _ws_client_token(websocket)
            for resolved, _sub_id in protected:
                if not provided or provided != _ws_auth_expected(resolved):
                    await websocket.close(code=1008)
                    return

        while True:
            try:
                message = await websocket.receive_text()
            except WebSocketDisconnect:
                return
            headers = dict(websocket.headers)
            query = dict(websocket.query_params)
            for resolved, subscription_id in matches:
                max_size = int(resolved.get("max_message_size") or 262_144)
                if len(message.encode("utf-8", errors="replace")) > max_size:
                    await websocket.send_json(
                        {"error": "message size exceeds the subscription limit"}
                    )
                    continue
                request = ProviderTriggerRequest(
                    headers=headers,
                    query=query,
                    body=message,
                    raw_body=message.encode("utf-8"),
                )
                try:
                    disp = await dispatch_provider_webhook(subscription_id, request)
                except KeyError:
                    continue
                await websocket.send_json(
                    {
                        "path": path,
                        "subscription_id": subscription_id,
                        "status": disp.status,
                        "runs": disp.run_ids,
                    }
                )
    except WebSocketDisconnect:
        pass
