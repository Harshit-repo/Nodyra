"""Discord v2 operation specs and executors."""

from __future__ import annotations

import json as json_mod
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.errors import ProviderError
from noodle_nodes.integrations_v2.registry import register_integration, register_operation
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

DISCORD_API_BASE = "https://discord.com/api/v10"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="discord_bot",
            key="*",
            label="Discord bot token",
            fields=["bot_token"],
            multi=True,
            test_service="discord_bot",
        ),
        description="Discord bot token.",
    )


DISCORD_SEND_MESSAGE_SPEC = OperationSpec(
    node_id="discord_send_message_v2",
    name="Discord Send Message",
    provider="discord",
    resource="message",
    operation="send",
    description="Send a message to a Discord channel.",
    icon="brand:discord",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="channel_id",
            required=True,
            placeholder="123456789012345678",
        ),
        OperationParamSpec(
            name="content",
            placeholder="Message text. Blank uses the input payload.",
        ),
        OperationParamSpec(
            name="embeds",
            type="array",
            group="Options",
            description="Optional Discord embed JSON array.",
        ),
        OperationParamSpec(
            name="tts",
            type="boolean",
            default=False,
            group="Options",
        ),
    ),
)

DISCORD_EDIT_MESSAGE_SPEC = OperationSpec(
    node_id="discord_edit_message_v2",
    name="Discord Edit Message",
    provider="discord",
    resource="message",
    operation="edit",
    description="Edit a Discord message.",
    icon="brand:discord",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="channel_id",
            required=True,
            placeholder="123456789012345678",
        ),
        OperationParamSpec(
            name="message_id",
            required=True,
            placeholder="123456789012345678",
        ),
        OperationParamSpec(
            name="content",
            placeholder="Updated message text. Blank uses the input payload.",
        ),
        OperationParamSpec(
            name="embeds",
            type="array",
            group="Options",
            description="Optional Discord embed JSON array.",
        ),
    ),
)

DISCORD_DELETE_MESSAGE_SPEC = OperationSpec(
    node_id="discord_delete_message_v2",
    name="Discord Delete Message",
    provider="discord",
    resource="message",
    operation="delete",
    description="Delete a Discord message.",
    icon="brand:discord",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="channel_id",
            required=True,
            placeholder="123456789012345678",
        ),
        OperationParamSpec(
            name="message_id",
            required=True,
            placeholder="123456789012345678",
        ),
    ),
)

DISCORD_GET_MESSAGES_SPEC = OperationSpec(
    node_id="discord_get_messages_v2",
    name="Discord Get Messages",
    provider="discord",
    resource="message",
    operation="list",
    description="Get recent messages from a Discord channel.",
    icon="brand:discord",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="channel_id",
            required=True,
            placeholder="123456789012345678",
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=50,
            group="Options",
            description="Number of messages to retrieve (max 100).",
        ),
    ),
)

DISCORD_LIST_CHANNELS_SPEC = OperationSpec(
    node_id="discord_list_channels_v2",
    name="Discord List Channels",
    provider="discord",
    resource="channel",
    operation="list",
    description="List channels in a Discord guild.",
    icon="brand:discord",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="guild_id",
            required=True,
            placeholder="123456789012345678",
        ),
    ),
)

DISCORD_LIST_GUILDS_SPEC = OperationSpec(
    node_id="discord_list_guilds_v2",
    name="Discord List Guilds",
    provider="discord",
    resource="guild",
    operation="list",
    description="List Discord guilds (servers) the bot is in.",
    icon="brand:discord",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="limit",
            type="number",
            default=100,
            group="Options",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"bot_token": value}
    return {}


def _bot_token(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("bot_token") or creds.get("token") or "")


def _transport(credentials: Any) -> ProviderTransport:
    token = _bot_token(credentials)
    if not token:
        raise ValueError("discord: bot token is required")
    return ProviderTransport(
        provider="discord",
        base_url=DISCORD_API_BASE,
        default_headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
    )


def _check_discord(response: Any, operation: str) -> Any:
    if isinstance(response, dict):
        code = response.get("code")
        if code is not None:
            raise ProviderError(
                provider="discord",
                operation=operation,
                status_code=response.get("status", 400),
                code=str(code),
                message=response.get("message", "discord_error"),
                retryable=False,
                response_body_summary=str(response),
            )
    return response


def _text_from_input(input_value: Any, content: str = "") -> str:
    if content:
        return content
    if input_value is None:
        return ""
    if isinstance(input_value, str):
        return input_value
    return json_mod.dumps(input_value, default=str)


def _embeds(value: Any, *, node_id: str) -> list[Any] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            parsed = json_mod.loads(value)
        except json_mod.JSONDecodeError as exc:
            raise ValueError(f"{node_id}: embeds must be JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"{node_id}: embeds must be a JSON array")
        return parsed
    if isinstance(value, list):
        return value
    raise ValueError(f"{node_id}: embeds must be a list or JSON array")


def send_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    channel_id: str = "",
    content: str = "",
    embeds: list[Any] | str | None = None,
    tts: bool = False,
) -> Any:
    if not channel_id:
        raise ValueError("discord_send_message_v2: channel_id is required")
    payload: dict[str, Any] = {
        "content": _text_from_input(input, content),
        "tts": bool(tts),
    }
    embed_payload = _embeds(embeds, node_id="discord_send_message_v2")
    if embed_payload is not None:
        payload["embeds"] = embed_payload
    result = _transport(credentials).request(
        "POST",
        f"/channels/{channel_id}/messages",
        operation="send_message",
        json_body=payload,
    )
    return _check_discord(result, "send_message")


def edit_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    channel_id: str = "",
    message_id: str = "",
    content: str = "",
    embeds: list[Any] | str | None = None,
) -> Any:
    if not channel_id:
        raise ValueError("discord_edit_message_v2: channel_id is required")
    if not message_id:
        raise ValueError("discord_edit_message_v2: message_id is required")
    payload: dict[str, Any] = {
        "content": _text_from_input(input, content),
    }
    embed_payload = _embeds(embeds, node_id="discord_edit_message_v2")
    if embed_payload is not None:
        payload["embeds"] = embed_payload
    result = _transport(credentials).request(
        "PATCH",
        f"/channels/{channel_id}/messages/{message_id}",
        operation="edit_message",
        json_body=payload,
    )
    return _check_discord(result, "edit_message")


def delete_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    channel_id: str = "",
    message_id: str = "",
) -> Any:
    if not channel_id:
        raise ValueError("discord_delete_message_v2: channel_id is required")
    if not message_id:
        raise ValueError("discord_delete_message_v2: message_id is required")
    result = _transport(credentials).request(
        "DELETE",
        f"/channels/{channel_id}/messages/{message_id}",
        operation="delete_message",
    )
    return _check_discord(result, "delete_message")


def get_messages(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    channel_id: str = "",
    limit: int = 50,
) -> Any:
    if not channel_id:
        raise ValueError("discord_get_messages_v2: channel_id is required")
    result = _transport(credentials).request(
        "GET",
        f"/channels/{channel_id}/messages",
        operation="get_messages",
        params={"limit": max(1, min(100, int(limit or 50)))},
    )
    return _check_discord(result, "get_messages")


def list_channels(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    guild_id: str = "",
) -> Any:
    if not guild_id:
        raise ValueError("discord_list_channels_v2: guild_id is required")
    result = _transport(credentials).request(
        "GET",
        f"/guilds/{guild_id}/channels",
        operation="list_channels",
    )
    return _check_discord(result, "list_channels")


def list_guilds(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 100,
) -> Any:
    result = _transport(credentials).request(
        "GET",
        "/users/@me/guilds",
        operation="list_guilds",
        params={"limit": max(1, min(200, int(limit or 100)))},
    )
    return _check_discord(result, "list_guilds")


register_operation(DISCORD_SEND_MESSAGE_SPEC, send_message, node_registry=None)
register_operation(DISCORD_EDIT_MESSAGE_SPEC, edit_message, node_registry=None)
register_operation(DISCORD_DELETE_MESSAGE_SPEC, delete_message, node_registry=None)
register_operation(DISCORD_GET_MESSAGES_SPEC, get_messages, node_registry=None)
register_operation(DISCORD_LIST_CHANNELS_SPEC, list_channels, node_registry=None)
register_operation(DISCORD_LIST_GUILDS_SPEC, list_guilds, node_registry=None)


DISCORD_INTEGRATION = IntegrationSpec(
    id="discord",
    name="Discord",
    description="Send and manage Discord messages, channels, and guilds.",
    icon="brand:discord",
    credential_types=("discord_bot",),
    resources=(
        ResourceSpec(
            id="message",
            name="Message",
            operations=(
                DISCORD_SEND_MESSAGE_SPEC,
                DISCORD_EDIT_MESSAGE_SPEC,
                DISCORD_DELETE_MESSAGE_SPEC,
                DISCORD_GET_MESSAGES_SPEC,
            ),
        ),
        ResourceSpec(
            id="channel",
            name="Channel",
            operations=(DISCORD_LIST_CHANNELS_SPEC,),
        ),
        ResourceSpec(
            id="guild",
            name="Guild",
            operations=(DISCORD_LIST_GUILDS_SPEC,),
        ),
    ),
)

register_integration(DISCORD_INTEGRATION)
