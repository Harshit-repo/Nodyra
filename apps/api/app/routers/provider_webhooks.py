"""Provider-managed webhook ingress."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.security import get_client_ip
from app.services.provider_triggers import (
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


async def _enforce_provider_webhook_rate_limit(
    subscription_id: str, request: Request
) -> None:
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


@router.api_route("/provider-webhook/{subscription_id}", methods=_METHODS)
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
