"""Telegram v2 operation specs and executors."""

from __future__ import annotations

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

TELEGRAM_API_BASE = "https://api.telegram.org"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="telegram_bot",
            key="*",
            label="Telegram bot token",
            fields=["bot_token"],
            multi=True,
            test_service="telegram_bot",
        ),
        description="Telegram bot token from @BotFather.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    token = str(creds.get("bot_token") or creds.get("token") or "")
    if not token:
        raise ValueError("telegram: bot_token is required")
    return ProviderTransport(
        provider="telegram",
        base_url=f"{TELEGRAM_API_BASE}/bot{token}",
        default_headers={"Content-Type": "application/json"},
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"bot_token": value}
    return {}


def _check(response: Any, operation: str) -> Any:
    if isinstance(response, dict) and response.get("ok") is False:
        code = str(response.get("error_code", "") or response.get("description", "telegram_error"))
        raise ProviderError(
            provider="telegram",
            operation=operation,
            status_code=response.get("error_code", 400),
            code=code,
            message=response.get("description", "telegram_api_error"),
            retryable=False,
            response_body_summary=str(response),
        )
    return response


def _text_from_input(input_value: Any, text: str = "") -> str:
    if text:
        return text
    if input_value is None:
        return ""
    return str(input_value)


TELEGRAM_SEND_MESSAGE_SPEC = OperationSpec(
    node_id="telegram_send_message_v2",
    name="Telegram Send Message",
    provider="telegram",
    resource="message",
    operation="send",
    description="Send a text message to a Telegram chat.",
    icon="brand:telegram",
    params=(
        _credentials_param(),
        OperationParamSpec(name="chat_id", required=True, placeholder="-1001234567890"),
        OperationParamSpec(
            name="text",
            placeholder="Message text. Blank uses the input payload.",
        ),
        OperationParamSpec(
            name="parse_mode",
            group="Options",
            choices=("", "HTML", "MarkdownV2"),
        ),
        OperationParamSpec(
            name="disable_notification",
            type="boolean",
            default=False,
            group="Options",
        ),
        OperationParamSpec(
            name="disable_web_page_preview",
            type="boolean",
            default=False,
            group="Options",
        ),
    ),
)

TELEGRAM_EDIT_MESSAGE_SPEC = OperationSpec(
    node_id="telegram_edit_message_v2",
    name="Telegram Edit Message",
    provider="telegram",
    resource="message",
    operation="edit",
    description="Edit a Telegram message.",
    icon="brand:telegram",
    params=(
        _credentials_param(),
        OperationParamSpec(name="chat_id", required=True),
        OperationParamSpec(name="message_id", required=True, placeholder="12345"),
        OperationParamSpec(name="text", multiline=True),
        OperationParamSpec(
            name="parse_mode",
            group="Options",
            choices=("", "HTML", "MarkdownV2"),
        ),
    ),
)

TELEGRAM_DELETE_MESSAGE_SPEC = OperationSpec(
    node_id="telegram_delete_message_v2",
    name="Telegram Delete Message",
    provider="telegram",
    resource="message",
    operation="delete",
    description="Delete a Telegram message.",
    icon="brand:telegram",
    params=(
        _credentials_param(),
        OperationParamSpec(name="chat_id", required=True),
        OperationParamSpec(name="message_id", required=True),
    ),
)

TELEGRAM_GET_UPDATES_SPEC = OperationSpec(
    node_id="telegram_get_updates_v2",
    name="Telegram Get Updates",
    provider="telegram",
    resource="update",
    operation="list",
    description="Get recent updates (messages) from the bot.",
    icon="brand:telegram",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="limit", type="number", default=50, group="Options"),
        OperationParamSpec(name="timeout", type="number", default=0, group="Options"),
    ),
)

TELEGRAM_GET_CHAT_SPEC = OperationSpec(
    node_id="telegram_get_chat_v2",
    name="Telegram Get Chat",
    provider="telegram",
    resource="chat",
    operation="get",
    description="Get information about a Telegram chat.",
    icon="brand:telegram",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="chat_id", required=True),
    ),
)

TELEGRAM_SEND_PHOTO_SPEC = OperationSpec(
    node_id="telegram_send_photo_v2",
    name="Telegram Send Photo",
    provider="telegram",
    resource="message",
    operation="send_photo",
    description="Send a photo to a Telegram chat.",
    icon="brand:telegram",
    params=(
        _credentials_param(),
        OperationParamSpec(name="chat_id", required=True),
        OperationParamSpec(name="photo_url", required=True, placeholder="https://..." ),
        OperationParamSpec(name="caption", group="Options"),
        OperationParamSpec(name="parse_mode", group="Options", choices=("", "HTML", "MarkdownV2")),
    ),
)


def send_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    chat_id: str = "",
    text: str = "",
    parse_mode: str = "",
    disable_notification: bool = False,
    disable_web_page_preview: bool = False,
) -> Any:
    if not chat_id:
        raise ValueError("telegram_send_message_v2: chat_id is required")
    body = _text_from_input(input, text)
    payload: dict[str, Any] = {"chat_id": chat_id, "text": body}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if disable_notification:
        payload["disable_notification"] = True
    if disable_web_page_preview:
        payload["disable_web_page_preview"] = True
    result = _transport(credentials).request(
        "POST",
        "/sendMessage",
        operation="send_message",
        json_body=payload,
    )
    return _check(result, "send_message")


def edit_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    chat_id: str = "",
    message_id: str = "",
    text: str = "",
    parse_mode: str = "",
) -> Any:
    if not chat_id or not message_id:
        raise ValueError("telegram_edit_message_v2: chat_id and message_id are required")
    body = _text_from_input(input, text)
    if not body:
        raise ValueError("telegram_edit_message_v2: text is required")
    payload: dict[str, Any] = {"chat_id": chat_id, "message_id": int(message_id), "text": body}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    result = _transport(credentials).request(
        "POST",
        "/editMessageText",
        operation="edit_message",
        json_body=payload,
    )
    return _check(result, "edit_message")


def delete_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    chat_id: str = "",
    message_id: str = "",
) -> Any:
    if not chat_id or not message_id:
        raise ValueError("telegram_delete_message_v2: chat_id and message_id are required")
    result = _transport(credentials).request(
        "POST",
        "/deleteMessage",
        operation="delete_message",
        json_body={"chat_id": chat_id, "message_id": int(message_id)},
    )
    return _check(result, "delete_message")


def get_updates(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 50,
    timeout: int = 0,
) -> Any:
    result = _transport(credentials).request(
        "GET",
        "/getUpdates",
        operation="get_updates",
        params={"limit": max(1, min(100, int(limit or 50))), "timeout": int(timeout or 0)},
    )
    return _check(result, "get_updates")


def get_chat(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    chat_id: str = "",
) -> Any:
    if not chat_id:
        raise ValueError("telegram_get_chat_v2: chat_id is required")
    result = _transport(credentials).request(
        "GET",
        "/getChat",
        operation="get_chat",
        params={"chat_id": chat_id},
    )
    return _check(result, "get_chat")


def send_photo(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    chat_id: str = "",
    photo_url: str = "",
    caption: str = "",
    parse_mode: str = "",
) -> Any:
    if not chat_id:
        raise ValueError("telegram_send_photo_v2: chat_id is required")
    if not photo_url:
        raise ValueError("telegram_send_photo_v2: photo_url is required")
    payload: dict[str, Any] = {"chat_id": chat_id, "photo": photo_url}
    if caption:
        payload["caption"] = caption
    if parse_mode:
        payload["parse_mode"] = parse_mode
    result = _transport(credentials).request(
        "POST",
        "/sendPhoto",
        operation="send_photo",
        json_body=payload,
    )
    return _check(result, "send_photo")


register_operation(TELEGRAM_SEND_MESSAGE_SPEC, send_message, node_registry=None)
register_operation(TELEGRAM_EDIT_MESSAGE_SPEC, edit_message, node_registry=None)
register_operation(TELEGRAM_DELETE_MESSAGE_SPEC, delete_message, node_registry=None)
register_operation(TELEGRAM_GET_UPDATES_SPEC, get_updates, node_registry=None)
register_operation(TELEGRAM_GET_CHAT_SPEC, get_chat, node_registry=None)
register_operation(TELEGRAM_SEND_PHOTO_SPEC, send_photo, node_registry=None)


TELEGRAM_INTEGRATION = IntegrationSpec(
    id="telegram",
    name="Telegram",
    description="Send and manage Telegram messages, photos, and chats.",
    icon="brand:telegram",
    credential_types=("telegram_bot",),
    resources=(
        ResourceSpec(
            id="message",
            name="Message",
            operations=(
                TELEGRAM_SEND_MESSAGE_SPEC,
                TELEGRAM_EDIT_MESSAGE_SPEC,
                TELEGRAM_DELETE_MESSAGE_SPEC,
                TELEGRAM_SEND_PHOTO_SPEC,
            ),
        ),
        ResourceSpec(
            id="update",
            name="Update",
            operations=(TELEGRAM_GET_UPDATES_SPEC,),
        ),
        ResourceSpec(
            id="chat",
            name="Chat",
            operations=(TELEGRAM_GET_CHAT_SPEC,),
        ),
    ),
)

register_integration(TELEGRAM_INTEGRATION)
