"""Gmail v2 operation specs and executors."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.providers.google import GoogleTransport
from nodyra_nodes.integrations_v2.registry import register_operation
from nodyra_nodes.integrations_v2.specs import (
    OperationParamSpec,
    OperationSpec,
)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="gmail_oauth2",
            key="*",
            label="Gmail OAuth2",
            fields=["access_token", "refresh_token"],
            multi=True,
            test_service="gmail",
        ),
        required_scopes=(GMAIL_SCOPE,),
    )


GMAIL_SEND_SPEC = OperationSpec(
    node_id="gmail_send",
    name="Gmail Send Email",
    provider="gmail",
    resource="message",
    operation="send",
    description="Send an email via Gmail.",
    icon="brand:gmail",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="to",
            required=True,
            placeholder="recipient@example.com",
            description="Recipient email address(es).",
        ),
        OperationParamSpec(
            name="subject",
            required=True,
            placeholder="Email subject",
            description="Email subject line.",
        ),
        OperationParamSpec(
            name="body",
            multiline=True,
            description="Email body. Blank uses the wired input.",
        ),
        OperationParamSpec(
            name="cc",
            group="Options",
            placeholder="cc@example.com",
            description="CC recipient(s).",
        ),
        OperationParamSpec(
            name="bcc",
            group="Options",
            placeholder="bcc@example.com",
            description="BCC recipient(s).",
        ),
        OperationParamSpec(
            name="content_type",
            group="Options",
            default="text/plain",
            choices=["text/plain", "text/html"],
            description="Body content type.",
        ),
    ),
)

GMAIL_LIST_MESSAGES_SPEC = OperationSpec(
    node_id="gmail_list_messages",
    name="Gmail List Messages",
    provider="gmail",
    resource="message",
    operation="list",
    description="List Gmail messages matching a query.",
    icon="brand:gmail",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="query",
            placeholder="from:alice@example.com after:2024/01/01",
            description="Gmail search query. Leave blank for inbox.",
        ),
        OperationParamSpec(
            name="max_results",
            type="number",
            default=50,
            group="Options",
            description="Maximum messages to return (max 500).",
        ),
        OperationParamSpec(
            name="include_spam_trash",
            type="boolean",
            default=False,
            group="Options",
            description="Include spam and trash in results.",
        ),
    ),
)

GMAIL_GET_MESSAGE_SPEC = OperationSpec(
    node_id="gmail_get_message",
    name="Gmail Get Message",
    provider="gmail",
    resource="message",
    operation="get",
    description="Get a single Gmail message by ID.",
    icon="brand:gmail",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="message_id",
            required=True,
            placeholder="Message ID",
            description="Gmail message ID.",
        ),
        OperationParamSpec(
            name="format",
            default="full",
            choices=["full", "metadata", "minimal", "raw"],
            group="Options",
            description="Message format.",
        ),
    ),
)

GMAIL_TRASH_MESSAGE_SPEC = OperationSpec(
    node_id="gmail_trash_message",
    name="Gmail Trash Message",
    provider="gmail",
    resource="message",
    operation="trash",
    description="Move a Gmail message to trash.",
    icon="brand:gmail",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="message_id",
            required=True,
            placeholder="Message ID",
            description="Gmail message ID to trash.",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str):
        return {"access_token": value}
    return {}


def _transport(credentials: Any) -> GoogleTransport:
    creds = _credentials_dict(credentials)
    return GoogleTransport(
        base_url=GMAIL_BASE,
        access_token=str(creds.get("access_token") or ""),
        api_key=str(creds.get("api_key") or ""),
    )


def _require(value: str, node_id: str, name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: {name} is required")
    return clean


def _encode_rfc2822(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def send(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    to: str = "",
    subject: str = "",
    body: str = "",
    cc: str = "",
    bcc: str = "",
    content_type: str = "text/plain",
) -> dict[str, Any]:
    _require(to, "gmail_send", "to")
    _require(subject, "gmail_send", "subject")

    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc

    payload = body if body else (str(input) if input is not None else "")
    if content_type == "text/html":
        msg.set_content(payload, subtype="html")
    else:
        msg.set_content(payload)

    raw = _encode_rfc2822(msg)
    resp = _transport(credentials).request(
        "POST",
        "/messages/send",
        operation="send",
        json_body={"raw": raw},
    )
    if isinstance(resp, dict):
        return {
            "id": resp.get("id", ""),
            "thread_id": resp.get("threadId", ""),
            "label_ids": resp.get("labelIds", []),
        }
    return resp


def list_messages(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    query: str = "",
    max_results: int = 50,
    include_spam_trash: bool = False,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "maxResults": min(500, max(1, int(max_results or 50))),
    }
    if query:
        params["q"] = query
    if include_spam_trash:
        params["includeSpamTrash"] = "true"

    transport = _transport(credentials)
    result = transport.request("GET", "/messages", operation="list_messages", params=params)
    if isinstance(result, dict):
        messages = result.get("messages", [])
        return {
            "messages": messages,
            "count": len(messages),
            "next_page_token": result.get("nextPageToken", ""),
        }
    return {"messages": [], "count": 0}


def get_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    message_id: str = "",
    format: str = "full",
) -> dict[str, Any]:
    mid = _require(message_id, "gmail_get_message", "message_id")
    fmt = str(format or "full").strip()
    if fmt not in ("full", "metadata", "minimal", "raw"):
        fmt = "full"

    return _transport(credentials).request(
        "GET",
        f"/messages/{quote(mid)}",
        operation="get_message",
        params={"format": fmt},
    )


def trash_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    message_id: str = "",
) -> dict[str, Any]:
    mid = _require(message_id, "gmail_trash_message", "message_id")
    return _transport(credentials).request(
        "POST",
        f"/messages/{quote(mid)}/trash",
        operation="trash_message",
    )


register_operation(GMAIL_SEND_SPEC, send)
register_operation(GMAIL_LIST_MESSAGES_SPEC, list_messages)
register_operation(GMAIL_GET_MESSAGE_SPEC, get_message)
register_operation(GMAIL_TRASH_MESSAGE_SPEC, trash_message)
