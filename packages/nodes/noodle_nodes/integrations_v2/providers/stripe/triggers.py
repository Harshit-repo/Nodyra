"""Stripe provider trigger spec and lifecycle hooks."""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerEvent,
    ProviderTriggerRequest,
    ProviderTriggerSpec,
)

_TIMESTAMP_TOLERANCE_SECONDS = 300


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _verify_stripe_signature(
    webhook_secret: str,
    raw_body: bytes,
    signature_header: str,
) -> bool:
    parts: dict[str, list[str]] = {}
    for chunk in signature_header.split(","):
        if "=" in chunk:
            key, _, val = chunk.partition("=")
            parts.setdefault(key.strip(), []).append(val.strip())
    try:
        ts = int((parts.get("t") or [""])[0])
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
        return False
    signed_payload = (
        f"{ts}.{raw_body.decode('utf-8', errors='replace')}".encode()
    )
    expected = hmac.new(
        webhook_secret.encode(), signed_payload, hashlib.sha256
    ).hexdigest()
    provided = parts.get("v1") or []
    return any(hmac.compare_digest(expected, v) for v in provided)


def _parse_event_types(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[,\n]", raw) if p.strip()]


def handle_stripe_event(
    request: ProviderTriggerRequest,
    params: dict[str, Any],
) -> ProviderTriggerEvent:
    webhook_secret = str(params.get("webhook_secret") or "").strip()
    headers = _lower_headers(request.headers)
    sig_header = headers.get("stripe-signature", "")

    if webhook_secret and not _verify_stripe_signature(
        webhook_secret, request.raw_body, sig_header
    ):
        return ProviderTriggerEvent(
            payload=None,
            response_body={"error": "Stripe signature verification failed"},
            response_status=401,
        )

    body = request.body if isinstance(request.body, dict) else {}
    event_type = str(body.get("type") or "")
    allowed = _parse_event_types(params.get("event_types"))
    if allowed and event_type not in allowed:
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "event type not subscribed"},
            response_status=200,
        )

    event_id = str(body.get("id") or "")
    return ProviderTriggerEvent(
        payload={
            "provider": "stripe",
            "event_type": event_type,
            "event_id": event_id,
            "livemode": body.get("livemode", False),
            "data": body.get("data"),
            "body": body,
        },
        dedupe_key=f"stripe:{event_id}" if event_id else None,
        response_body={"received": True},
        response_status=202,
    )


STRIPE_EVENT_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="stripe_event_trigger_v2",
    name="Stripe Event Trigger",
    provider="stripe",
    resource="event",
    event="webhook",
    description=(
        "Start a workflow on Stripe webhook events. "
        "Create a webhook endpoint in your Stripe dashboard pointing to the "
        "callback URL shown in this node."
    ),
    icon="brand:stripe",
    params=(
        OperationParamSpec(
            name="webhook_secret",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="generic",
                key="value",
                label="Webhook signing secret",
                fields=["value"],
                multi=False,
            ),
            description=(
                "Stripe webhook signing secret (whsec_...) from the Stripe dashboard."
            ),
        ),
        OperationParamSpec(
            name="event_types",
            default="",
            placeholder="payment_intent.succeeded, invoice.paid",
            description=(
                "Comma-separated Stripe event types to accept. "
                "Leave blank to accept all."
            ),
        ),
    ),
    handle_event=lambda request, params: handle_stripe_event(request, params),
)


register_provider_trigger(STRIPE_EVENT_TRIGGER_SPEC)
