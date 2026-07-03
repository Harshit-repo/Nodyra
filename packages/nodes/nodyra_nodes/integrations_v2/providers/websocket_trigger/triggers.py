"""WebSocket trigger — fires on incoming WebSocket messages."""

from __future__ import annotations

import json
from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_provider_trigger
from nodyra_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerActivationContext,
    ProviderTriggerDeactivationContext,
    ProviderTriggerEvent,
    ProviderTriggerRequest,
    ProviderTriggerSpec,
    ProviderTriggerSubscription,
)

_DEFAULT_MAX_MESSAGE_SIZE = 262_144  # 256 KB


def activate_websocket(
    ctx: ProviderTriggerActivationContext,
) -> ProviderTriggerSubscription:
    """Register the WebSocket path with the Nodyra runtime."""
    params = ctx.params
    path = str(params.get("path") or "ws").strip("/")
    return ProviderTriggerSubscription(
        external_id=f"ws-trigger:{path}:{ctx.callback_url}",
    )


def deactivate_websocket(ctx: ProviderTriggerDeactivationContext) -> None:
    """Unregister the WebSocket path (no-op — runtime cleans up on subscription removal)."""


def handle_websocket_event(
    request: ProviderTriggerRequest,
    params: dict[str, Any],
) -> ProviderTriggerEvent:
    """Process an incoming WebSocket message and convert to a trigger event."""
    message_format = str(params.get("message_format") or "json").lower()
    max_size = int(params.get("max_message_size") or _DEFAULT_MAX_MESSAGE_SIZE)

    body = request.body
    if isinstance(body, (bytes, bytearray)):
        if len(body) > max_size:
            raise ValueError(
                f"websocket_trigger: message size {len(body)} exceeds max {max_size}"
            )
        if message_format == "json":
            payload = json.loads(body.decode("utf-8", errors="replace"))
        elif message_format == "text":
            payload = {"message": body.decode("utf-8", errors="replace")}
        else:
            payload = {"message": body.hex(), "encoding": "hex"}
    elif isinstance(body, str):
        if len(body.encode()) > max_size:
            raise ValueError(
                f"websocket_trigger: message size exceeds max {max_size}"
            )
        if message_format == "json":
            payload = json.loads(body)
        else:
            payload = {"message": body}
    elif isinstance(body, dict):
        payload = body
    else:
        payload = {"message": str(body)}

    # Enrich with connection metadata from headers
    client_id = request.headers.get("x-nodyra-client-id", "")
    event_type = request.headers.get("x-nodyra-ws-event", "message")

    return ProviderTriggerEvent(
        payload={
            "event": event_type,
            "client_id": client_id,
            **payload,
        }
    )


WEBSOCKET_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="websocket_trigger",
    name="WebSocket",
    provider="websocket",
    resource="connection",
    event="message",
    description=(
        "Fire a workflow on incoming WebSocket messages. "
        "Nodyra opens a WebSocket endpoint at /ws/triggers/{path}."
    ),
    icon="globe",
    params=(
        OperationParamSpec(
            name="path",
            default="ws",
            description="URL path for the WebSocket endpoint (appended to /ws/triggers/).",
        ),
        OperationParamSpec(
            name="auth_type",
            choices=["none", "token", "header"],
            default="none",
            description="Authentication method for incoming connections.",
        ),
        OperationParamSpec(
            name="auth_token",
            type="credential",
            credential=CredentialSpec(
                type="api_key",
                key="token",
                label="Auth token",
                fields=["token"],
                multi=False,
                test_service="",
            ),
            description="Token for 'token' or 'header' auth types.",
        ),
        OperationParamSpec(
            name="message_format",
            choices=["json", "text", "binary"],
            default="json",
            description="Expected format of incoming messages.",
        ),
        OperationParamSpec(
            name="max_message_size",
            type="number",
            default=_DEFAULT_MAX_MESSAGE_SIZE,
            group="Advanced",
            description="Maximum allowed message size in bytes (default 256 KB).",
            advanced=True,
        ),
    ),
    activate=activate_websocket,
    deactivate=deactivate_websocket,
    handle_event=handle_websocket_event,
)

register_provider_trigger(WEBSOCKET_TRIGGER_SPEC)
