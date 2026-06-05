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

OUTLOOK_LIST_MESSAGE_ATTACHMENTS_SPEC = OperationSpec(
    node_id="outlook_list_message_attachments_v2",
    name="Outlook List Message Attachments",
    provider="microsoft_outlook",
    resource="attachment",
    operation="list",
    description="List attachments for an Outlook message.",
    icon="brand:microsoftoutlook",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="message_id", required=True),
        OperationParamSpec(name="limit", type="number", default=25, group="Options"),
    ),
)

OUTLOOK_GET_MESSAGE_ATTACHMENT_SPEC = OperationSpec(
    node_id="outlook_get_message_attachment_v2",
    name="Outlook Get Message Attachment",
    provider="microsoft_outlook",
    resource="attachment",
    operation="get",
    description="Get attachment metadata and inline content when Graph returns it.",
    icon="brand:microsoftoutlook",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="message_id", required=True),
        OperationParamSpec(name="attachment_id", required=True),
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

OUTLOOK_CREATE_DRAFT_SPEC = OperationSpec(
    node_id="outlook_create_draft_v2",
    name="Outlook Create Draft",
    provider="microsoft_outlook",
    resource="message",
    operation="create_draft",
    description="Create an email draft in Outlook.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((MAIL_READWRITE_SCOPE,)),
        OperationParamSpec(name="to", placeholder="recipient@example.com"),
        OperationParamSpec(name="subject", placeholder="Draft subject"),
        OperationParamSpec(name="body", multiline=True),
        OperationParamSpec(
            name="content_type",
            default="HTML",
            choices=("HTML", "Text"),
            group="Options",
        ),
        OperationParamSpec(name="cc", group="Options"),
        OperationParamSpec(name="bcc", group="Options"),
        OperationParamSpec(name="reply_to", group="Options"),
    ),
)

OUTLOOK_REPLY_MESSAGE_SPEC = OperationSpec(
    node_id="outlook_reply_message_v2",
    name="Outlook Reply To Message",
    provider="microsoft_outlook",
    resource="message",
    operation="reply",
    description="Reply to an Outlook message.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((MAIL_SEND_SCOPE,)),
        OperationParamSpec(name="message_id", required=True),
        OperationParamSpec(name="comment", multiline=True, description="Blank uses input."),
    ),
)

OUTLOOK_FORWARD_MESSAGE_SPEC = OperationSpec(
    node_id="outlook_forward_message_v2",
    name="Outlook Forward Message",
    provider="microsoft_outlook",
    resource="message",
    operation="forward",
    description="Forward an Outlook message to one or more recipients.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((MAIL_SEND_SCOPE,)),
        OperationParamSpec(name="message_id", required=True),
        OperationParamSpec(name="to", required=True, placeholder="recipient@example.com"),
        OperationParamSpec(name="comment", multiline=True, group="Options"),
    ),
)

OUTLOOK_SEND_DRAFT_SPEC = OperationSpec(
    node_id="outlook_send_draft_v2",
    name="Outlook Send Draft",
    provider="microsoft_outlook",
    resource="message",
    operation="send_draft",
    description="Send an existing Outlook draft message.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((MAIL_SEND_SCOPE,)),
        OperationParamSpec(name="message_id", required=True),
    ),
)

OUTLOOK_UPDATE_MESSAGE_SPEC = OperationSpec(
    node_id="outlook_update_message_v2",
    name="Outlook Update Message",
    provider="microsoft_outlook",
    resource="message",
    operation="update",
    description="Update mutable Outlook message fields such as read state or categories.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((MAIL_READWRITE_SCOPE,)),
        OperationParamSpec(name="message_id", required=True),
        OperationParamSpec(name="is_read", type="boolean", default=None, group="Fields"),
        OperationParamSpec(name="categories", type="array", group="Fields"),
    ),
)

OUTLOOK_DELETE_MESSAGE_SPEC = OperationSpec(
    node_id="outlook_delete_message_v2",
    name="Outlook Delete Message",
    provider="microsoft_outlook",
    resource="message",
    operation="delete",
    description="Delete an Outlook message.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((MAIL_READWRITE_SCOPE,)),
        OperationParamSpec(name="message_id", required=True),
    ),
)

OUTLOOK_CREATE_CALENDAR_EVENT_SPEC = OperationSpec(
    node_id="outlook_create_calendar_event_v2",
    name="Outlook Create Calendar Event",
    provider="microsoft_outlook",
    resource="calendar",
    operation="create_event",
    description="Create an event in the default Outlook calendar.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((CALENDAR_READWRITE_SCOPE,)),
        OperationParamSpec(name="subject", required=True, placeholder="Customer call"),
        OperationParamSpec(name="start_datetime", required=True, placeholder="2024-01-01T10:00:00"),
        OperationParamSpec(name="end_datetime", required=True, placeholder="2024-01-01T10:30:00"),
        OperationParamSpec(name="time_zone", default="UTC", group="Options"),
        OperationParamSpec(name="body", multiline=True, group="Options"),
        OperationParamSpec(
            name="content_type",
            default="HTML",
            choices=("HTML", "Text"),
            group="Options",
        ),
        OperationParamSpec(name="location", group="Options"),
        OperationParamSpec(name="attendees", type="array", group="Options"),
        OperationParamSpec(name="is_all_day", type="boolean", default=False, group="Options"),
    ),
)

OUTLOOK_UPDATE_CALENDAR_EVENT_SPEC = OperationSpec(
    node_id="outlook_update_calendar_event_v2",
    name="Outlook Update Calendar Event",
    provider="microsoft_outlook",
    resource="calendar",
    operation="update_event",
    description="Update an event in the default Outlook calendar.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((CALENDAR_READWRITE_SCOPE,)),
        OperationParamSpec(name="event_id", required=True),
        OperationParamSpec(name="subject", group="Fields"),
        OperationParamSpec(name="start_datetime", group="Fields"),
        OperationParamSpec(name="end_datetime", group="Fields"),
        OperationParamSpec(name="time_zone", default="UTC", group="Fields"),
        OperationParamSpec(name="body", multiline=True, group="Fields"),
        OperationParamSpec(
            name="content_type",
            default="HTML",
            choices=("HTML", "Text"),
            group="Fields",
        ),
        OperationParamSpec(name="location", group="Fields"),
        OperationParamSpec(name="attendees", type="array", group="Fields"),
        OperationParamSpec(name="is_all_day", type="boolean", default=None, group="Fields"),
    ),
)

OUTLOOK_DELETE_CALENDAR_EVENT_SPEC = OperationSpec(
    node_id="outlook_delete_calendar_event_v2",
    name="Outlook Delete Calendar Event",
    provider="microsoft_outlook",
    resource="calendar",
    operation="delete_event",
    description="Delete an event from the default Outlook calendar.",
    icon="brand:microsoftoutlook",
    params=(
        _credentials_param((CALENDAR_READWRITE_SCOPE,)),
        OperationParamSpec(name="event_id", required=True),
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


def _string_list(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [str(raw).strip()]


def _require(value: str, node_id: str, name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: '{name}' is required")
    return clean


def _text_from_input(input_value: Any) -> str:
    if input_value is None:
        return ""
    if isinstance(input_value, str):
        return input_value
    return str(input_value)


def _message_payload(
    *,
    to: str = "",
    subject: str = "",
    body: str = "",
    content_type: str = "HTML",
    cc: str = "",
    bcc: str = "",
    reply_to: str = "",
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": content_type, "content": body},
    }
    if to:
        message["toRecipients"] = _address_list(to)
    if cc:
        message["ccRecipients"] = _address_list(cc)
    if bcc:
        message["bccRecipients"] = _address_list(bcc)
    if reply_to:
        message["replyTo"] = _address_list(reply_to)
    return message


def _attendee_list(raw: Any) -> list[dict[str, Any]]:
    return [
        {"emailAddress": {"address": address}, "type": "required"}
        for address in _string_list(raw)
    ]


def _event_payload(
    *,
    subject: str = "",
    start_datetime: str = "",
    end_datetime: str = "",
    time_zone: str = "UTC",
    body: str = "",
    content_type: str = "HTML",
    location: str = "",
    attendees: Any = None,
    is_all_day: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if subject:
        payload["subject"] = subject
    if body:
        payload["body"] = {"contentType": content_type, "content": body}
    if start_datetime:
        payload["start"] = {"dateTime": start_datetime, "timeZone": time_zone or "UTC"}
    if end_datetime:
        payload["end"] = {"dateTime": end_datetime, "timeZone": time_zone or "UTC"}
    if location:
        payload["location"] = {"displayName": location}
    attendee_payload = _attendee_list(attendees)
    if attendee_payload:
        payload["attendees"] = attendee_payload
    if is_all_day is not None:
        payload["isAllDay"] = bool(is_all_day)
    return payload


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

    message = _message_payload(
        to=to,
        subject=subject,
        body=body,
        content_type=content_type,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
    )

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


def list_message_attachments(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    message_id: str = "",
    limit: int = 25,
) -> Any:
    message = _require(message_id, "outlook_list_message_attachments_v2", "message_id")
    return _transport(credentials).request(
        "GET",
        f"/me/messages/{message}/attachments",
        operation="list_message_attachments",
        params={"$top": max(1, min(100, int(limit or 25)))},
    )


def get_message_attachment(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    message_id: str = "",
    attachment_id: str = "",
) -> Any:
    message = _require(message_id, "outlook_get_message_attachment_v2", "message_id")
    attachment = _require(
        attachment_id,
        "outlook_get_message_attachment_v2",
        "attachment_id",
    )
    return _transport(credentials).request(
        "GET",
        f"/me/messages/{message}/attachments/{attachment}",
        operation="get_message_attachment",
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


def create_draft(
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
) -> Any:
    return _transport(credentials).request(
        "POST",
        "/me/messages",
        operation="create_draft",
        json_body=_message_payload(
            to=to,
            subject=subject,
            body=body,
            content_type=content_type,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
        ),
    )


def reply_message(
    *,
    input: Any = None,
    credentials: dict | None = None,
    message_id: str = "",
    comment: str = "",
) -> Any:
    message = _require(message_id, "outlook_reply_message_v2", "message_id")
    body = comment or _text_from_input(input)
    if not body:
        raise ValueError("outlook_reply_message_v2: 'comment' is required")
    return _transport(credentials).request(
        "POST",
        f"/me/messages/{message}/reply",
        operation="reply_message",
        json_body={"comment": body},
    )


def forward_message(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    message_id: str = "",
    to: str = "",
    comment: str = "",
) -> Any:
    message = _require(message_id, "outlook_forward_message_v2", "message_id")
    recipients = _address_list(_require(to, "outlook_forward_message_v2", "to"))
    return _transport(credentials).request(
        "POST",
        f"/me/messages/{message}/forward",
        operation="forward_message",
        json_body={"comment": comment, "toRecipients": recipients},
    )


def send_draft(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    message_id: str = "",
) -> Any:
    message = _require(message_id, "outlook_send_draft_v2", "message_id")
    return _transport(credentials).request(
        "POST",
        f"/me/messages/{message}/send",
        operation="send_draft",
        json_body={},
    )


def update_message(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    message_id: str = "",
    is_read: bool | None = None,
    categories: list[str] | str | None = None,
) -> Any:
    message = _require(message_id, "outlook_update_message_v2", "message_id")
    payload: dict[str, Any] = {}
    if is_read is not None:
        payload["isRead"] = bool(is_read)
    category_values = _string_list(categories)
    if category_values:
        payload["categories"] = category_values
    if not payload:
        raise ValueError("outlook_update_message_v2: at least one field is required")
    return _transport(credentials).request(
        "PATCH",
        f"/me/messages/{message}",
        operation="update_message",
        json_body=payload,
    )


def delete_message(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    message_id: str = "",
) -> Any:
    message = _require(message_id, "outlook_delete_message_v2", "message_id")
    return _transport(credentials).request(
        "DELETE",
        f"/me/messages/{message}",
        operation="delete_message",
    )


def create_calendar_event(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    subject: str = "",
    start_datetime: str = "",
    end_datetime: str = "",
    time_zone: str = "UTC",
    body: str = "",
    content_type: str = "HTML",
    location: str = "",
    attendees: list[str] | str | None = None,
    is_all_day: bool = False,
) -> Any:
    _require(subject, "outlook_create_calendar_event_v2", "subject")
    _require(start_datetime, "outlook_create_calendar_event_v2", "start_datetime")
    _require(end_datetime, "outlook_create_calendar_event_v2", "end_datetime")
    return _transport(credentials).request(
        "POST",
        "/me/events",
        operation="create_calendar_event",
        json_body=_event_payload(
            subject=subject,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            time_zone=time_zone,
            body=body,
            content_type=content_type,
            location=location,
            attendees=attendees,
            is_all_day=is_all_day,
        ),
    )


def update_calendar_event(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    event_id: str = "",
    subject: str = "",
    start_datetime: str = "",
    end_datetime: str = "",
    time_zone: str = "UTC",
    body: str = "",
    content_type: str = "HTML",
    location: str = "",
    attendees: list[str] | str | None = None,
    is_all_day: bool | None = None,
) -> Any:
    event = _require(event_id, "outlook_update_calendar_event_v2", "event_id")
    payload = _event_payload(
        subject=subject,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        time_zone=time_zone,
        body=body,
        content_type=content_type,
        location=location,
        attendees=attendees,
        is_all_day=is_all_day,
    )
    if not payload:
        raise ValueError("outlook_update_calendar_event_v2: at least one field is required")
    return _transport(credentials).request(
        "PATCH",
        f"/me/events/{event}",
        operation="update_calendar_event",
        json_body=payload,
    )


def delete_calendar_event(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict | None = None,
    event_id: str = "",
) -> Any:
    event = _require(event_id, "outlook_delete_calendar_event_v2", "event_id")
    return _transport(credentials).request(
        "DELETE",
        f"/me/events/{event}",
        operation="delete_calendar_event",
    )


register_operation(OUTLOOK_SEND_MAIL_SPEC, send_mail)
register_operation(OUTLOOK_LIST_MESSAGES_SPEC, list_messages)
register_operation(OUTLOOK_GET_MESSAGE_SPEC, get_message)
register_operation(OUTLOOK_LIST_MESSAGE_ATTACHMENTS_SPEC, list_message_attachments)
register_operation(OUTLOOK_GET_MESSAGE_ATTACHMENT_SPEC, get_message_attachment)
register_operation(OUTLOOK_LIST_CALENDAR_EVENTS_SPEC, list_calendar_events)
register_operation(OUTLOOK_CREATE_DRAFT_SPEC, create_draft)
register_operation(OUTLOOK_REPLY_MESSAGE_SPEC, reply_message)
register_operation(OUTLOOK_FORWARD_MESSAGE_SPEC, forward_message)
register_operation(OUTLOOK_SEND_DRAFT_SPEC, send_draft)
register_operation(OUTLOOK_UPDATE_MESSAGE_SPEC, update_message)
register_operation(OUTLOOK_DELETE_MESSAGE_SPEC, delete_message)
register_operation(OUTLOOK_CREATE_CALENDAR_EVENT_SPEC, create_calendar_event)
register_operation(OUTLOOK_UPDATE_CALENDAR_EVENT_SPEC, update_calendar_event)
register_operation(OUTLOOK_DELETE_CALENDAR_EVENT_SPEC, delete_calendar_event)
