"""Provider-managed webhook ingress."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.services.provider_triggers import (
    dispatch_provider_webhook,
)
from noodle_nodes.integrations_v2.specs import ProviderTriggerRequest

router = APIRouter(tags=["provider-webhooks"])

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


async def _provider_request(request: Request) -> ProviderTriggerRequest:
    raw = await request.body()
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


@router.api_route("/provider-webhook/{subscription_id}", methods=_METHODS)
async def provider_webhook(subscription_id: str, request: Request) -> Response:
    """Receive a delivery for a lifecycle-managed provider trigger."""
    from app.tenancy import run_as_system

    # Cross-org by design: the subscription decides which org's workflow
    # fires; the external provider has no Noodle identity. start_run pins
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
