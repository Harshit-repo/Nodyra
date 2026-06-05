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
    name="Slack Send Message",
    provider="slack",
    resource="message",
    operation="send",
    description="Send a Slack message using the v2 provider transport.",
    icon="brand:slack",
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

SLACK_REPLY_IN_THREAD_SPEC = OperationSpec(
    node_id="slack_reply_in_thread_v2",
    name="Slack Reply In Thread",
    provider="slack",
    resource="message",
    operation="reply_thread",
    description="Post a Slack reply in an existing message thread.",
    icon="brand:slack",
    params=(
        _credentials_param(),
        OperationParamSpec(name="channel", required=True, placeholder="C0123456789"),
        OperationParamSpec(
            name="thread_ts",
            required=True,
            placeholder="1712345678.000100",
        ),
        OperationParamSpec(
            name="text",
            placeholder="Reply text. Blank uses the input payload.",
        ),
        OperationParamSpec(
            name="blocks",
            type="array",
            group="Options",
            description="Optional Slack Block Kit JSON array.",
        ),
        OperationParamSpec(
            name="reply_broadcast",
            type="boolean",
            default=False,
            group="Options",
        ),
    ),
)

SLACK_UPDATE_MESSAGE_SPEC = OperationSpec(
    node_id="slack_update_message_v2",
    name="Slack Update Message",
    provider="slack",
    resource="message",
    operation="update",
    description="Update a Slack message using chat.update.",
    icon="brand:slack",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="channel",
            required=True,
            placeholder="C0123456789",
            description="Channel containing the message.",
        ),
        OperationParamSpec(
            name="ts",
            required=True,
            placeholder="1712345678.000100",
            description="Timestamp of the message to update.",
        ),
        OperationParamSpec(
            name="text",
            placeholder="Updated message text. Blank uses the input payload.",
        ),
        OperationParamSpec(
            name="blocks",
            type="array",
            group="Options",
            description="Optional Slack Block Kit JSON array.",
        ),
    ),
)

SLACK_DELETE_MESSAGE_SPEC = OperationSpec(
    node_id="slack_delete_message_v2",
    name="Slack Delete Message",
    provider="slack",
    resource="message",
    operation="delete",
    description="Delete a Slack message using chat.delete.",
    icon="brand:slack",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="channel",
            required=True,
            placeholder="C0123456789",
            description="Channel containing the message.",
        ),
        OperationParamSpec(
            name="ts",
            required=True,
            placeholder="1712345678.000100",
            description="Timestamp of the message to delete.",
        ),
    ),
)

SLACK_ADD_REACTION_SPEC = OperationSpec(
    node_id="slack_add_reaction_v2",
    name="Slack Add Reaction",
    provider="slack",
    resource="reaction",
    operation="add",
    description="Add an emoji reaction to a Slack message.",
    icon="brand:slack",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="channel",
            required=True,
            placeholder="C0123456789",
            description="Channel where the message was posted.",
        ),
        OperationParamSpec(
            name="timestamp",
            required=True,
            placeholder="1712345678.000100",
            description="Timestamp of the message to react to.",
        ),
        OperationParamSpec(
            name="reaction_name",
            required=True,
            placeholder="thumbsup",
            description="Emoji reaction name without surrounding colons.",
        ),
    ),
)

SLACK_LIST_CHANNELS_SPEC = OperationSpec(
    node_id="slack_list_channels_v2",
    name="Slack List Channels",
    provider="slack",
    resource="channel",
    operation="list",
    description="List Slack channels and conversations.",
    icon="brand:slack",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="types",
            default="public_channel,private_channel",
            group="Options",
            description="Comma-separated conversation types to return.",
        ),
        OperationParamSpec(
            name="exclude_archived",
            type="boolean",
            default=True,
            group="Options",
            description="Exclude archived conversations.",
        ),
        OperationParamSpec(
            name="max_results",
            type="number",
            default=100,
            group="Options",
            description="Maximum channels to return.",
        ),
    ),
)

SLACK_LIST_USERS_SPEC = OperationSpec(
    node_id="slack_list_users_v2",
    name="Slack List Users",
    provider="slack",
    resource="user",
    operation="list",
    description="List Slack workspace users.",
    icon="brand:slack",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="include_deleted",
            type="boolean",
            default=False,
            group="Options",
            description="Include deactivated/deleted users.",
        ),
        OperationParamSpec(
            name="max_results",
            type="number",
            default=100,
            group="Options",
            description="Maximum users to return.",
        ),
    ),
)

SLACK_OPEN_DIRECT_MESSAGE_SPEC = OperationSpec(
    node_id="slack_open_direct_message_v2",
    name="Slack Open Direct Message",
    provider="slack",
    resource="conversation",
    operation="open_dm",
    description="Open or resume a direct-message conversation with Slack users.",
    icon="brand:slack",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="users",
            type="array",
            required=True,
            placeholder="U0123456789",
            description="One or more Slack user IDs.",
        ),
        OperationParamSpec(
            name="return_im",
            type="boolean",
            default=True,
            group="Options",
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


def _blocks(value: Any, *, node_id: str) -> list[Any] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{node_id}: blocks must be JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"{node_id}: blocks must be a JSON array")
        return parsed
    if isinstance(value, list):
        return value
    raise ValueError(f"{node_id}: blocks must be a list or JSON array")


def _max_results(value: int) -> int:
    try:
        raw = int(value or 100)
    except (TypeError, ValueError):
        raw = 100
    return max(1, min(1000, raw))


def _user_ids(value: Any) -> str:
    if isinstance(value, str):
        ids = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        ids = [str(item).strip() for item in value if str(item).strip()]
    else:
        ids = []
    if not ids:
        raise ValueError("slack_open_direct_message_v2: users is required")
    return ",".join(ids)


def _paginate_slack_cursor(
    transport: ProviderTransport,
    path: str,
    *,
    operation: str,
    items_key: str,
    params: dict[str, Any] | None = None,
    max_results: int = 100,
) -> list[Any]:
    target = _max_results(max_results)
    collected: list[Any] = []
    page_size = min(200, target)
    request_params: dict[str, Any] = {**(params or {}), "limit": page_size}

    while len(collected) < target:
        payload = _check_slack_ok(
            transport.request("GET", path, operation=operation, params=request_params),
            operation,
        )
        if not isinstance(payload, dict):
            break
        items = payload.get(items_key) or []
        if isinstance(items, list):
            collected.extend(items[: max(0, target - len(collected))])
        metadata = payload.get("response_metadata")
        cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else ""
        if not cursor:
            break
        request_params["cursor"] = cursor
        request_params["limit"] = min(200, max(1, target - len(collected)))

    return collected


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
    block_payload = _blocks(blocks, node_id="slack_send_message_v2")
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


def reply_in_thread(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    channel: str = "",
    thread_ts: str = "",
    text: str = "",
    blocks: list[Any] | str | None = None,
    reply_broadcast: bool = False,
) -> Any:
    if not channel:
        raise ValueError("slack_reply_in_thread_v2: channel is required")
    if not thread_ts:
        raise ValueError("slack_reply_in_thread_v2: thread_ts is required")
    payload: dict[str, Any] = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": _text_from_input(input, text),
        "reply_broadcast": bool(reply_broadcast),
    }
    block_payload = _blocks(blocks, node_id="slack_reply_in_thread_v2")
    if block_payload is not None:
        payload["blocks"] = block_payload
    result = _transport(credentials).request(
        "POST",
        "/chat.postMessage",
        operation="reply_in_thread",
        json_body=payload,
    )
    return _check_slack_ok(result, "reply_in_thread")


def update_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    channel: str = "",
    ts: str = "",
    text: str = "",
    blocks: list[Any] | str | None = None,
) -> Any:
    if not channel:
        raise ValueError("slack_update_message_v2: channel is required")
    if not ts:
        raise ValueError("slack_update_message_v2: ts is required")
    payload: dict[str, Any] = {"channel": channel, "ts": ts}
    message_text = _text_from_input(input, text)
    block_payload = _blocks(blocks, node_id="slack_update_message_v2")
    if message_text:
        payload["text"] = message_text
    if block_payload is not None:
        payload["blocks"] = block_payload
    if "text" not in payload and "blocks" not in payload:
        raise ValueError("slack_update_message_v2: text, blocks, or input is required")
    result = _transport(credentials).request(
        "POST",
        "/chat.update",
        operation="update_message",
        json_body=payload,
    )
    return _check_slack_ok(result, "update_message")


def delete_message(
    *,
    input: Any = None,  # noqa: ARG001 - input ignored
    credentials: dict[str, str] | None = None,
    channel: str = "",
    ts: str = "",
) -> Any:
    if not channel:
        raise ValueError("slack_delete_message_v2: channel is required")
    if not ts:
        raise ValueError("slack_delete_message_v2: ts is required")
    result = _transport(credentials).request(
        "POST",
        "/chat.delete",
        operation="delete_message",
        json_body={"channel": channel, "ts": ts},
    )
    return _check_slack_ok(result, "delete_message")


def add_reaction(
    *,
    input: Any = None,  # noqa: ARG001 - input ignored
    credentials: dict[str, str] | None = None,
    channel: str = "",
    timestamp: str = "",
    reaction_name: str = "",
) -> Any:
    if not channel:
        raise ValueError("slack_add_reaction_v2: channel is required")
    if not timestamp:
        raise ValueError("slack_add_reaction_v2: timestamp is required")
    clean_name = str(reaction_name or "").strip().strip(":")
    if not clean_name:
        raise ValueError("slack_add_reaction_v2: reaction_name is required")
    result = _transport(credentials).request(
        "POST",
        "/reactions.add",
        operation="add_reaction",
        json_body={"channel": channel, "timestamp": timestamp, "name": clean_name},
    )
    return _check_slack_ok(result, "add_reaction")


def list_channels(
    *,
    input: Any = None,  # noqa: ARG001 - input ignored
    credentials: dict[str, str] | None = None,
    types: str = "public_channel,private_channel",
    exclude_archived: bool = True,
    max_results: int = 100,
) -> dict[str, Any]:
    params = {
        "types": types or "public_channel,private_channel",
        "exclude_archived": "true" if exclude_archived else "false",
    }
    channels = _paginate_slack_cursor(
        _transport(credentials),
        "/conversations.list",
        operation="list_channels",
        items_key="channels",
        params=params,
        max_results=max_results,
    )
    return {"channels": channels, "count": len(channels)}


def list_users(
    *,
    input: Any = None,  # noqa: ARG001 - input ignored
    credentials: dict[str, str] | None = None,
    include_deleted: bool = False,
    max_results: int = 100,
) -> dict[str, Any]:
    members = _paginate_slack_cursor(
        _transport(credentials),
        "/users.list",
        operation="list_users",
        items_key="members",
        max_results=max_results,
    )
    if not include_deleted:
        members = [
            member
            for member in members
            if not (isinstance(member, dict) and member.get("deleted"))
        ]
    return {"members": members, "count": len(members)}


def open_direct_message(
    *,
    input: Any = None,  # noqa: ARG001 - input ignored
    credentials: dict[str, str] | None = None,
    users: list[str] | str | None = None,
    return_im: bool = True,
) -> Any:
    result = _transport(credentials).request(
        "POST",
        "/conversations.open",
        operation="open_direct_message",
        json_body={"users": _user_ids(users), "return_im": bool(return_im)},
    )
    return _check_slack_ok(result, "open_direct_message")


register_operation(SLACK_SEND_MESSAGE_SPEC, send_message)
register_operation(SLACK_REPLY_IN_THREAD_SPEC, reply_in_thread)
register_operation(SLACK_UPDATE_MESSAGE_SPEC, update_message)
register_operation(SLACK_DELETE_MESSAGE_SPEC, delete_message)
register_operation(SLACK_ADD_REACTION_SPEC, add_reaction)
register_operation(SLACK_LIST_CHANNELS_SPEC, list_channels)
register_operation(SLACK_LIST_USERS_SPEC, list_users)
register_operation(SLACK_OPEN_DIRECT_MESSAGE_SPEC, open_direct_message)
