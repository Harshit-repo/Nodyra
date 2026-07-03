"""Slack provider trigger spec and lifecycle hooks."""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_provider_trigger
from nodyra_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerEvent,
    ProviderTriggerRequest,
    ProviderTriggerSpec,
)

_TIMESTAMP_TOLERANCE_SECONDS = 300


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _verify_slack_signature(
    signing_secret: str,
    raw_body: bytes,
    headers: dict[str, str],
) -> bool:
    h = _lower_headers(headers)
    timestamp_str = h.get("x-slack-request-timestamp", "")
    signature = h.get("x-slack-signature", "")
    try:
        ts = int(timestamp_str)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
        return False
    basestring = (
        f"v0:{ts}:{raw_body.decode('utf-8', errors='replace')}".encode()
    )
    expected = "v0=" + hmac.new(
        signing_secret.encode(), basestring, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def _parse_event_types(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[,\n]", raw) if p.strip()]


def handle_slack_event(
    request: ProviderTriggerRequest,
    params: dict[str, Any],
) -> ProviderTriggerEvent:
    signing_secret = str(params.get("signing_secret") or "").strip()
    if signing_secret and not _verify_slack_signature(
        signing_secret, request.raw_body, request.headers
    ):
        return ProviderTriggerEvent(
            payload=None,
            response_body={"error": "Slack signature verification failed"},
            response_status=401,
        )

    body = request.body if isinstance(request.body, dict) else {}
    event_type = str(body.get("type") or "")

    if event_type == "url_verification":
        return ProviderTriggerEvent(
            payload=None,
            response_body={"challenge": body.get("challenge", "")},
            response_status=200,
        )

    if event_type != "event_callback":
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "ignored"},
            response_status=200,
        )

    inner_event = body.get("event") or {}
    inner_type = str(inner_event.get("type") or "")
    allowed = _parse_event_types(params.get("event_types"))
    if allowed and inner_type not in allowed:
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "event type not subscribed"},
            response_status=200,
        )

    event_id = str(body.get("event_id") or "")
    return ProviderTriggerEvent(
        payload={
            "provider": "slack",
            "event_type": inner_type,
            "team_id": str(body.get("team_id") or ""),
            "event_id": event_id,
            "event": inner_event,
            "authorizations": body.get("authorizations"),
        },
        dedupe_key=f"slack:{event_id}" if event_id else None,
        response_body={"message": "accepted"},
        response_status=202,
    )


SLACK_EVENT_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="slack_event_trigger_v2",
    name="Slack Event Trigger",
    provider="slack",
    resource="event",
    event="callback",
    description=(
        "Start a workflow when a Slack event fires. "
        "Configure your Slack app's Event Subscriptions URL to point to the "
        "callback URL shown in this node."
    ),
    icon="brand:slack",
    params=(
        OperationParamSpec(
            name="signing_secret",
            type="credential",
            required=True,
            credential=CredentialSpec(
                type="generic",
                key="value",
                label="Signing secret",
                fields=["value"],
                multi=False,
            ),
            description="Slack app signing secret used to verify event payloads.",
        ),
        OperationParamSpec(
            name="event_types",
            default="message",
            placeholder="message, reaction_added, member_joined_channel",
            description=(
                "Comma-separated Slack event types to accept. "
                "Leave blank to accept all."
            ),
        ),
    ),
    handle_event=lambda request, params: handle_slack_event(request, params),
)


register_provider_trigger(SLACK_EVENT_TRIGGER_SPEC)
