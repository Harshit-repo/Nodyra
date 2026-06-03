"""Slack v2 operation specs and executors."""

from __future__ import annotations

import json
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.errors import ProviderError
from noodle_nodes.integrations_v2.registry import register_operation
from noodle_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from noodle_nodes.integrations_v2.transport import ProviderTransport

SLACK_API_BASE = "https://slack.com/api"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="slack_bot",
            key="*",
            label="Slack bot token",
            fields=["bot_token"],
            multi=True,
            test_service="slack_bot",
        ),
        description="Slack bot token.",
    )


SLACK_SEND_MESSAGE_SPEC = OperationSpec(
    node_id="slack_send_message_v2",
    name="Slack Send Message V2",
    provider="slack",
    resource="message",
    operation="send",
    description="Send a Slack message using the v2 provider transport.",
    icon="message",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="channel",
            required=True,
            placeholder="C0123456789 or #alerts",
        ),
        OperationParamSpec(
            name="text",
            placeholder="Message text. Blank uses the input payload.",
        ),
        OperationParamSpec(
            name="blocks",
            type="array",
            group="Options",
            description="Optional Slack Block Kit JSON array.",
        ),
        OperationParamSpec(
            name="thread_ts",
            group="Options",
            placeholder="Optional parent message timestamp.",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str):
        return {"bot_token": value}
    return {}


def _bot_token(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("bot_token") or creds.get("token") or creds.get("api_key") or "")


def _transport(credentials: Any) -> ProviderTransport:
    token = _bot_token(credentials)
    if not token:
        raise ValueError("slack_send_message_v2: credentials are required")
    return ProviderTransport(
        provider="slack",
        base_url=SLACK_API_BASE,
        default_headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )


def _text_from_input(input_value: Any, text: str = "") -> str:
    if text:
        return text
    if input_value is None:
        return ""
    if isinstance(input_value, str):
        return input_value
    return json.dumps(input_value, default=str)


def _blocks(value: Any) -> list[Any] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"slack_send_message_v2: blocks must be JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError("slack_send_message_v2: blocks must be a JSON array")
        return parsed
    if isinstance(value, list):
        return value
    raise ValueError("slack_send_message_v2: blocks must be a list or JSON array")


def _check_slack_ok(payload: Any, operation: str) -> Any:
    if isinstance(payload, dict) and payload.get("ok") is False:
        code = str(payload.get("error") or "slack_error")
        raise ProviderError(
            provider="slack",
            operation=operation,
            status_code=200,
            code=code,
            message=code,
            retryable=False,
            response_body_summary=str({"ok": False, "error": code}),
        )
    return payload


def send_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    channel: str = "",
    text: str = "",
    blocks: list[Any] | str | None = None,
    thread_ts: str = "",
) -> Any:
    if not channel:
        raise ValueError("slack_send_message_v2: channel is required")
    payload: dict[str, Any] = {
        "channel": channel,
        "text": _text_from_input(input, text),
    }
    block_payload = _blocks(blocks)
    if block_payload is not None:
        payload["blocks"] = block_payload
    if thread_ts:
        payload["thread_ts"] = thread_ts
    result = _transport(credentials).request(
        "POST",
        "/chat.postMessage",
        operation="send_message",
        json_body=payload,
    )
    return _check_slack_ok(result, "send_message")


register_operation(SLACK_SEND_MESSAGE_SPEC, send_message)
