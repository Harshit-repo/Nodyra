"""Microsoft Outlook v2 operation specs and executors.

Uses Microsoft Graph API v1.0.  Credential type is ``microsoft_outlook_oauth2``
with the standard MS Graph delegated-permission scopes.
"""

from __future__ import annotations

from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.providers.microsoft import MicrosoftGraphTransport
from noodle_nodes.integrations_v2.registry import register_operation
from noodle_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec

# Delegated-permission scopes needed by each operation group
MAIL_READ_SCOPE = "Mail.Read"
MAIL_SEND_SCOPE = "Mail.Send"
MAIL_READWRITE_SCOPE = "Mail.ReadWrite"
CALENDAR_READ_SCOPE = "Calendars.Read"
CALENDAR_READWRITE_SCOPE = "Calendars.ReadWrite"


def _credentials_param(extra_scopes: tuple[str, ...] = ()) -> OperationParamSpec:
    scopes = (MAIL_READ_SCOPE,) + extra_scopes
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="microsoft_outlook_oauth2",
            key="*",
            label="Microsoft Outlook OAuth2",
            fields=["access_token", "refresh_token"],
            multi=True,
            test_service="microsoft_outlook",
        ),
        required_scopes=scopes,
    )


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

OUTLOOK_SEND_MAIL_SPEC = OperationSpec(
    node_id="outlook_send_mail_v2",
    name="Outlook Send Email",
    provider="microsoft_outlook",
    resource="message",
    operation="send",
    description="Send an email via Microsoft Outlook using the Graph API.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((MAIL_SEND_SCOPE,)),
        OperationParamSpec(
            name="to",
            required=True,
            placeholder="recipient@example.com",
            description="Recipient email address (or comma-separated list).",
        ),
        OperationParamSpec(
            name="subject",
            required=True,
            placeholder="Hello from Noodle",
        ),
        OperationParamSpec(
            name="body",
            required=True,
            multiline=True,
            description="Email body (plain text or HTML).",
        ),
        OperationParamSpec(
            name="content_type",
            default="HTML",
            choices=("HTML", "Text"),
            group="Options",
            description="Body content type.",
        ),
        OperationParamSpec(
            name="cc",
            placeholder="cc@example.com",
            group="Options",
            description="CC address(es), comma-separated.",
        ),
        OperationParamSpec(
            name="bcc",
            placeholder="bcc@example.com",
            group="Options",
            description="BCC address(es), comma-separated.",
        ),
        OperationParamSpec(
            name="reply_to",
            placeholder="noreply@example.com",
            group="Options",
        ),
        OperationParamSpec(
            name="save_to_sent_items",
            type="boolean",
            default=True,
            group="Options",
        ),
    ),
)

OUTLOOK_LIST_MESSAGES_SPEC = OperationSpec(
    node_id="outlook_list_messages_v2",
    name="Outlook List Messages",
    provider="microsoft_outlook",
    resource="message",
    operation="list",
    description="List email messages from a mailbox folder.",
    icon="brand:microsoftoutlook",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="folder_id",
            default="Inbox",
            description="Folder name or well-known name (Inbox, SentItems, Drafts, ...).",
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=25,
            description="Maximum number of messages to return (1–100).",
        ),
        OperationParamSpec(
            name="search",
            placeholder="subject:invoice",
            group="Options",
            description="OData $search query string.",
        ),
        OperationParamSpec(
            name="filter",
            placeholder="isRead eq false",
            group="Options",
            description="OData $filter expression.",
        ),
        OperationParamSpec(
            name="select",
            default="id,subject,from,receivedDateTime,bodyPreview,isRead",
            group="Options",
            description="Comma-separated fields to include in each message.",
        ),
        OperationParamSpec(
            name="order_by",
            default="receivedDateTime desc",
            group="Options",
        ),
    ),
)

OUTLOOK_GET_MESSAGE_SPEC = OperationSpec(
    node_id="outlook_get_message_v2",
    name="Outlook Get Message",
    provider="microsoft_outlook",
    resource="message",
    operation="get",
    description="Get a single email message by its ID.",
    icon="brand:microsoftoutlook",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="message_id",
            required=True,
            description="Graph API message ID.",
        ),
        OperationParamSpec(
            name="select",
            default="id,subject,from,receivedDateTime,body,isRead",
            group="Options",
            description="Comma-separated fields to return.",
        ),
    ),
)

OUTLOOK_LIST_CALENDAR_EVENTS_SPEC = OperationSpec(
    node_id="outlook_list_calendar_events_v2",
    name="Outlook List Calendar Events",
    provider="microsoft_outlook",
    resource="calendar",
    operation="list_events",
    description="List calendar events from the default calendar.",
    icon="brand:microsoftoutlook",
    tool_side_effecting=False,
    params=(
        _credentials_param((CALENDAR_READ_SCOPE,)),
        OperationParamSpec(
            name="start_datetime",
            placeholder="2024-01-01T00:00:00Z",
            description="Start of date range (ISO 8601).",
        ),
        OperationParamSpec(
            name="end_datetime",
            placeholder="2024-01-31T23:59:59Z",
            description="End of date range (ISO 8601).",
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=25,
            description="Maximum events to return.",
        ),
        OperationParamSpec(
            name="select",
            default="id,subject,start,end,location,organizer,isAllDay",
            group="Options",
        ),
        OperationParamSpec(
            name="order_by",
            default="start/dateTime",
            group="Options",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _credentials_dict(value: Any) -> dict[str, str]:
    return value if isinstance(value, dict) else {}


def _transport(credentials: Any) -> MicrosoftGraphTransport:
    creds = _credentials_dict(credentials)
    return MicrosoftGraphTransport(
        access_token=str(creds.get("access_token") or ""),
    )


def _address_list(raw: str) -> list[dict]:
    """Convert a comma-separated email string to Graph recipient objects."""
    return [
        {"emailAddress": {"address": addr.strip()}}
        for addr in raw.split(",")
        if addr.strip()
    ]


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def send_mail(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    to: str = "",
    subject: str = "",
    body: str = "",
    content_type: str = "HTML",
    cc: str = "",
    bcc: str = "",
    reply_to: str = "",
    save_to_sent_items: bool = True,
) -> Any:
    """Send an email via Microsoft Graph sendMail."""
    if not to:
        raise ValueError("outlook_send_mail_v2: 'to' is required")
    if not subject:
        raise ValueError("outlook_send_mail_v2: 'subject' is required")

    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": content_type, "content": body},
        "toRecipients": _address_list(to),
    }
    if cc:
        message["ccRecipients"] = _address_list(cc)
    if bcc:
        message["bccRecipients"] = _address_list(bcc)
    if reply_to:
        message["replyTo"] = _address_list(reply_to)

    return _transport(credentials).request(
        "POST",
        "/me/sendMail",
        operation="send_mail",
        json_body={
            "message": message,
            "saveToSentItems": str(save_to_sent_items).lower(),
        },
    )


def list_messages(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    folder_id: str = "Inbox",
    limit: int = 25,
    search: str = "",
    filter: str = "",  # noqa: A002
    select: str = "id,subject,from,receivedDateTime,bodyPreview,isRead",
    order_by: str = "receivedDateTime desc",
) -> Any:
    """List messages in a mail folder, returning up to ``limit`` items."""
    top = max(1, min(100, int(limit or 25)))
    params: dict[str, Any] = {"$top": top}
    if select:
        params["$select"] = select
    if search:
        params["$search"] = f'"{search}"'
    elif filter:
        params["$filter"] = filter
    if order_by and not search:
        # $orderby and $search cannot be combined
        params["$orderby"] = order_by

    return _transport(credentials).request(
        "GET",
        f"/me/mailFolders/{folder_id}/messages",
        operation="list_messages",
        params=params,
    )


def get_message(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    message_id: str = "",
    select: str = "id,subject,from,receivedDateTime,body,isRead",
) -> Any:
    """Get a single message by its Graph API ID."""
    if not message_id:
        raise ValueError("outlook_get_message_v2: 'message_id' is required")
    params: dict[str, Any] = {}
    if select:
        params["$select"] = select
    return _transport(credentials).request(
        "GET",
        f"/me/messages/{message_id}",
        operation="get_message",
        params=params,
    )


def list_calendar_events(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    start_datetime: str = "",
    end_datetime: str = "",
    limit: int = 25,
    select: str = "id,subject,start,end,location,organizer,isAllDay",
    order_by: str = "start/dateTime",
) -> Any:
    """List calendar events within an optional date range."""
    top = max(1, min(100, int(limit or 25)))
    params: dict[str, Any] = {"$top": top}
    if select:
        params["$select"] = select
    if order_by:
        params["$orderby"] = order_by
    if start_datetime or end_datetime:
        filters = []
        if start_datetime:
            filters.append(f"start/dateTime ge '{start_datetime}'")
        if end_datetime:
            filters.append(f"end/dateTime le '{end_datetime}'")
        params["$filter"] = " and ".join(filters)
    return _transport(credentials).request(
        "GET",
        "/me/events",
        operation="list_calendar_events",
        params=params,
    )


register_operation(OUTLOOK_SEND_MAIL_SPEC, send_mail)
register_operation(OUTLOOK_LIST_MESSAGES_SPEC, list_messages)
register_operation(OUTLOOK_GET_MESSAGE_SPEC, get_message)
register_operation(OUTLOOK_LIST_CALENDAR_EVENTS_SPEC, list_calendar_events)
