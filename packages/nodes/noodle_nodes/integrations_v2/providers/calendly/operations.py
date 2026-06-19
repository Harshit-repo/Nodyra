"""Calendly v2 operation specs and executors."""

from __future__ import annotations

from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_integration, register_operation
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

CALENDLY_API_BASE = "https://api.calendly.com"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="calendly_pat",
            key="*",
            label="Calendly personal access token",
            fields=["access_token"],
            multi=True,
            test_service="calendly_pat",
        ),
        description="Calendly personal access token.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    token = str(creds.get("access_token") or creds.get("token") or "")
    if not token:
        raise ValueError("calendly: access_token is required")
    return ProviderTransport(
        provider="calendly",
        base_url=CALENDLY_API_BASE,
        default_headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"access_token": value}
    return {}


CALENDLY_GET_USER_SPEC = OperationSpec(
    node_id="calendly_get_user_v2",
    name="Calendly Get User",
    provider="calendly",
    resource="user",
    operation="get",
    description="Get the current Calendly user.",
    icon="brand:calendly",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
    ),
)

CALENDLY_LIST_EVENTS_SPEC = OperationSpec(
    node_id="calendly_list_events_v2",
    name="Calendly List Events",
    provider="calendly",
    resource="event",
    operation="list",
    description="List Calendly scheduled events.",
    icon="brand:calendly",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="user_uri",
            required=True,
            placeholder="https://api.calendly.com/users/...",
        ),
        OperationParamSpec(name="limit", type="number", default=20, group="Options"),
        OperationParamSpec(
            name="status",
            choices=("active", "canceled"),
            group="Options",
        ),
    ),
)

CALENDLY_GET_EVENT_SPEC = OperationSpec(
    node_id="calendly_get_event_v2",
    name="Calendly Get Event",
    provider="calendly",
    resource="event",
    operation="get",
    description="Get details of a Calendly scheduled event.",
    icon="brand:calendly",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="event_uuid", required=True),
    ),
)

CALENDLY_LIST_EVENT_TYPES_SPEC = OperationSpec(
    node_id="calendly_list_event_types_v2",
    name="Calendly List Event Types",
    provider="calendly",
    resource="event_type",
    operation="list",
    description="List Calendly event types for a user.",
    icon="brand:calendly",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="user_uri",
            required=True,
            placeholder="https://api.calendly.com/users/...",
        ),
    ),
)


def get_user(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/users/me",
        operation="get_user",
    )


def list_events(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    user_uri: str = "",
    limit: int = 20,
    status: str = "",
) -> Any:
    if not user_uri:
        raise ValueError("calendly_list_events_v2: user_uri is required")
    params: dict[str, Any] = {
        "user": user_uri,
        "count": max(1, min(100, int(limit or 20))),
    }
    if status:
        params["status"] = status
    return _transport(credentials).request(
        "GET",
        "/scheduled_events",
        operation="list_events",
        params=params,
    )


def get_event(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    event_uuid: str = "",
) -> Any:
    if not event_uuid:
        raise ValueError("calendly_get_event_v2: event_uuid is required")
    return _transport(credentials).request(
        "GET",
        f"/scheduled_events/{event_uuid}",
        operation="get_event",
    )


def list_event_types(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    user_uri: str = "",
) -> Any:
    if not user_uri:
        raise ValueError("calendly_list_event_types_v2: user_uri is required")
    return _transport(credentials).request(
        "GET",
        "/event_types",
        operation="list_event_types",
        params={"user": user_uri},
    )


register_operation(CALENDLY_GET_USER_SPEC, get_user, node_registry=None)
register_operation(CALENDLY_LIST_EVENTS_SPEC, list_events, node_registry=None)
register_operation(CALENDLY_GET_EVENT_SPEC, get_event, node_registry=None)
register_operation(CALENDLY_LIST_EVENT_TYPES_SPEC, list_event_types, node_registry=None)

CALENDLY_INTEGRATION = IntegrationSpec(
    id="calendly",
    name="Calendly",
    description="Get user info, list and get scheduled events, and list event types.",
    icon="brand:calendly",
    credential_types=("calendly_pat",),
    resources=(
        ResourceSpec(
            id="user",
            name="User",
            operations=(CALENDLY_GET_USER_SPEC,),
        ),
        ResourceSpec(
            id="event",
            name="Event",
            operations=(
                CALENDLY_LIST_EVENTS_SPEC,
                CALENDLY_GET_EVENT_SPEC,
            ),
        ),
        ResourceSpec(
            id="event_type",
            name="Event Type",
            operations=(CALENDLY_LIST_EVENT_TYPES_SPEC,),
        ),
    ),
)

register_integration(CALENDLY_INTEGRATION)
