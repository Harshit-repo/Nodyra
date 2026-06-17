"""Google Calendar v2 operation specs and executors."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.providers.google import GoogleTransport
from noodle_nodes.integrations_v2.registry import register_operation
from noodle_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="google_calendar_oauth2",
            key="*",
            label="Google Calendar OAuth2",
            fields=["access_token", "refresh_token"],
            multi=True,
            test_service="google_calendar",
        ),
        required_scopes=(CALENDAR_SCOPE,),
    )


GOOGLE_CALENDAR_LIST_EVENTS_SPEC = OperationSpec(
    node_id="google_calendar_list_events",
    name="Google Calendar List Events",
    provider="google_calendar",
    resource="events",
    operation="list",
    description="List upcoming events from a Google Calendar.",
    icon="brand:googlecalendar",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="calendar_id",
            default="primary",
            placeholder="primary",
            description="Calendar ID (default: primary).",
        ),
        OperationParamSpec(
            name="max_results",
            type="number",
            default=50,
            group="Options",
            description="Maximum events to return (max 2500).",
        ),
        OperationParamSpec(
            name="time_min",
            group="Options",
            placeholder="2024-01-01T00:00:00Z",
            description="Start of time range (ISO 8601). Defaults to now.",
        ),
        OperationParamSpec(
            name="time_max",
            group="Options",
            placeholder="2024-12-31T23:59:59Z",
            description="End of time range (ISO 8601).",
        ),
        OperationParamSpec(
            name="query",
            group="Options",
            placeholder="search term",
            description="Free-text search term.",
        ),
        OperationParamSpec(
            name="single_events",
            type="boolean",
            default=True,
            group="Options",
            description="Expand recurring events into individual instances.",
        ),
        OperationParamSpec(
            name="order_by",
            default="startTime",
            group="Options",
            choices=["startTime", "updated"],
            description="Sort order.",
        ),
    ),
)

GOOGLE_CALENDAR_CREATE_EVENT_SPEC = OperationSpec(
    node_id="google_calendar_create_event",
    name="Google Calendar Create Event",
    provider="google_calendar",
    resource="events",
    operation="create",
    description="Create a new event on a Google Calendar.",
    icon="brand:googlecalendar",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="calendar_id",
            default="primary",
            placeholder="primary",
            description="Calendar ID (default: primary).",
        ),
        OperationParamSpec(
            name="summary",
            required=True,
            placeholder="Event title",
            description="Event title.",
        ),
        OperationParamSpec(
            name="start_time",
            required=True,
            placeholder="2024-01-01T10:00:00",
            description="Start time (ISO 8601).",
        ),
        OperationParamSpec(
            name="end_time",
            required=True,
            placeholder="2024-01-01T11:00:00",
            description="End time (ISO 8601).",
        ),
        OperationParamSpec(
            name="description",
            multiline=True,
            group="Options",
            placeholder="Event description",
        ),
        OperationParamSpec(
            name="location",
            group="Options",
            placeholder="Conference Room A",
            description="Event location.",
        ),
        OperationParamSpec(
            name="timezone",
            group="Options",
            placeholder="America/New_York",
            description="IANA timezone (default: UTC).",
        ),
        OperationParamSpec(
            name="attendees",
            group="Options",
            placeholder="alice@example.com, bob@example.com",
            description="Comma-separated attendee emails.",
        ),
    ),
)

GOOGLE_CALENDAR_UPDATE_EVENT_SPEC = OperationSpec(
    node_id="google_calendar_update_event",
    name="Google Calendar Update Event",
    provider="google_calendar",
    resource="events",
    operation="update",
    description="Update an existing Google Calendar event.",
    icon="brand:googlecalendar",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="calendar_id",
            default="primary",
            placeholder="primary",
            description="Calendar ID (default: primary).",
        ),
        OperationParamSpec(
            name="event_id",
            required=True,
            placeholder="_abcd1234...",
            description="ID of the event to update.",
        ),
        OperationParamSpec(
            name="summary",
            placeholder="Updated title",
            description="New event title. Blank keeps existing.",
        ),
        OperationParamSpec(
            name="start_time",
            placeholder="2024-01-01T10:00:00",
            description="New start time (ISO 8601).",
        ),
        OperationParamSpec(
            name="end_time",
            placeholder="2024-01-01T11:00:00",
            description="New end time (ISO 8601).",
        ),
        OperationParamSpec(
            name="description",
            multiline=True,
            group="Options",
            placeholder="Updated description",
        ),
    ),
)

GOOGLE_CALENDAR_DELETE_EVENT_SPEC = OperationSpec(
    node_id="google_calendar_delete_event",
    name="Google Calendar Delete Event",
    provider="google_calendar",
    resource="events",
    operation="delete",
    description="Delete a Google Calendar event.",
    icon="brand:googlecalendar",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="calendar_id",
            default="primary",
            placeholder="primary",
            description="Calendar ID (default: primary).",
        ),
        OperationParamSpec(
            name="event_id",
            required=True,
            placeholder="_abcd1234...",
            description="ID of the event to delete.",
        ),
    ),
)

GOOGLE_CALENDAR_GET_EVENT_SPEC = OperationSpec(
    node_id="google_calendar_get_event",
    name="Google Calendar Get Event",
    provider="google_calendar",
    resource="events",
    operation="get",
    description="Get a single Google Calendar event by ID.",
    icon="brand:googlecalendar",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="calendar_id",
            default="primary",
            placeholder="primary",
            description="Calendar ID (default: primary).",
        ),
        OperationParamSpec(
            name="event_id",
            required=True,
            placeholder="_abcd1234...",
            description="ID of the event to retrieve.",
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
        base_url=CALENDAR_BASE,
        access_token=str(creds.get("access_token") or ""),
        api_key=str(creds.get("api_key") or ""),
    )


def _calendar_id(value: str) -> str:
    return str(value or "primary").strip() or "primary"


def _validate_event_id(value: str, node_id: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: event_id is required")
    return clean


def list_events(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    calendar_id: str = "primary",
    max_results: int = 50,
    time_min: str = "",
    time_max: str = "",
    query: str = "",
    single_events: bool = True,
    order_by: str = "startTime",
) -> dict[str, Any]:
    cal = _calendar_id(calendar_id)
    params: dict[str, Any] = {
        "maxResults": min(2500, max(1, int(max_results or 50))),
        "singleEvents": str(single_events).lower(),
    }
    if order_by in ("startTime", "updated"):
        params["orderBy"] = order_by
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    if query:
        params["q"] = query

    transport = _transport(credentials)
    response = transport.request(
        "GET",
        f"/calendars/{quote(cal)}/events",
        operation="list_events",
        params=params,
    )
    if isinstance(response, dict):
        items = response.get("items", [])
        return {"events": items, "count": len(items)}
    return {"events": [], "count": 0}


def create_event(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    calendar_id: str = "primary",
    summary: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
    location: str = "",
    timezone: str = "",
    attendees: str = "",
) -> dict[str, Any]:
    cal = _calendar_id(calendar_id)
    if not summary:
        raise ValueError("google_calendar_create_event: summary is required")
    if not start_time:
        raise ValueError("google_calendar_create_event: start_time is required")
    if not end_time:
        raise ValueError("google_calendar_create_event: end_time is required")

    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start_time, "timeZone": timezone or "UTC"},
        "end": {"dateTime": end_time, "timeZone": timezone or "UTC"},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [
            {"email": email.strip()}
            for email in str(attendees).split(",")
            if email.strip()
        ]

    return _transport(credentials).request(
        "POST",
        f"/calendars/{quote(cal)}/events",
        operation="create_event",
        json_body=body,
    )


def update_event(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    calendar_id: str = "primary",
    event_id: str = "",
    summary: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
) -> dict[str, Any]:
    cal = _calendar_id(calendar_id)
    eid = _validate_event_id(event_id, "google_calendar_update_event")

    body: dict[str, Any] = {}
    if summary:
        body["summary"] = summary
    if start_time:
        body["start"] = {"dateTime": start_time, "timeZone": "UTC"}
    if end_time:
        body["end"] = {"dateTime": end_time, "timeZone": "UTC"}
    if description:
        body["description"] = description
    if not body:
        raise ValueError("google_calendar_update_event: at least one field to update is required")

    return _transport(credentials).request(
        "PATCH",
        f"/calendars/{quote(cal)}/events/{quote(eid)}",
        operation="update_event",
        json_body=body,
    )


def delete_event(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    calendar_id: str = "primary",
    event_id: str = "",
) -> dict[str, Any]:
    cal = _calendar_id(calendar_id)
    eid = _validate_event_id(event_id, "google_calendar_delete_event")
    return _transport(credentials).request(
        "DELETE",
        f"/calendars/{quote(cal)}/events/{quote(eid)}",
        operation="delete_event",
    )


def get_event(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    calendar_id: str = "primary",
    event_id: str = "",
) -> dict[str, Any]:
    cal = _calendar_id(calendar_id)
    eid = _validate_event_id(event_id, "google_calendar_get_event")
    return _transport(credentials).request(
        "GET",
        f"/calendars/{quote(cal)}/events/{quote(eid)}",
        operation="get_event",
    )


register_operation(GOOGLE_CALENDAR_LIST_EVENTS_SPEC, list_events)
register_operation(GOOGLE_CALENDAR_CREATE_EVENT_SPEC, create_event)
register_operation(GOOGLE_CALENDAR_UPDATE_EVENT_SPEC, update_event)
register_operation(GOOGLE_CALENDAR_DELETE_EVENT_SPEC, delete_event)
register_operation(GOOGLE_CALENDAR_GET_EVENT_SPEC, get_event)
