"""Zoom v2 operation specs and executors."""

from __future__ import annotations

from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_integration, register_operation
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from nodyra_nodes.integrations_v2.transport import ProviderTransport

ZOOM_API_BASE = "https://api.zoom.us/v2"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="zoom",
            key="*",
            label="Zoom access token",
            fields=["access_token"],
            multi=True,
            test_service="zoom",
        ),
        description="Zoom personal access token or server-to-server OAuth access token.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    token = str(creds.get("access_token") or creds.get("token") or "")
    if not token:
        raise ValueError("zoom: access_token is required")
    return ProviderTransport(
        provider="zoom",
        base_url=ZOOM_API_BASE,
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


ZOOM_CREATE_MEETING_SPEC = OperationSpec(
    node_id="zoom_create_meeting_v2",
    name="Zoom Create Meeting",
    provider="zoom",
    resource="meeting",
    operation="create",
    description="Create a Zoom meeting.",
    icon="brand:zoom",
    params=(
        _credentials_param(),
        OperationParamSpec(name="topic", required=True, placeholder="Weekly Standup"),
        OperationParamSpec(name="user_id", default="me"),
        OperationParamSpec(
            name="type",
            choices=(1, 2, 3),
            default=2,
            group="Options",
        ),
        OperationParamSpec(
            name="start_time",
            placeholder="2025-01-01T10:00:00Z",
            group="Options",
        ),
        OperationParamSpec(name="duration", type="number", group="Options"),
        OperationParamSpec(name="password", group="Options"),
        OperationParamSpec(name="agenda", multiline=True, group="Options"),
    ),
)

ZOOM_GET_MEETING_SPEC = OperationSpec(
    node_id="zoom_get_meeting_v2",
    name="Zoom Get Meeting",
    provider="zoom",
    resource="meeting",
    operation="get",
    description="Get details of a Zoom meeting.",
    icon="brand:zoom",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="meeting_id", required=True, placeholder="123456789"),
    ),
)

ZOOM_LIST_MEETINGS_SPEC = OperationSpec(
    node_id="zoom_list_meetings_v2",
    name="Zoom List Meetings",
    provider="zoom",
    resource="meeting",
    operation="list",
    description="List Zoom meetings for a user.",
    icon="brand:zoom",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="user_id", default="me"),
        OperationParamSpec(name="limit", type="number", default=30, group="Options"),
        OperationParamSpec(
            name="type",
            choices=("scheduled", "live", "upcoming"),
            default="scheduled",
            group="Options",
        ),
    ),
)

ZOOM_DELETE_MEETING_SPEC = OperationSpec(
    node_id="zoom_delete_meeting_v2",
    name="Zoom Delete Meeting",
    provider="zoom",
    resource="meeting",
    operation="delete",
    description="Delete a Zoom meeting.",
    icon="brand:zoom",
    params=(
        _credentials_param(),
        OperationParamSpec(name="meeting_id", required=True),
    ),
)


def create_meeting(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    topic: str = "",
    user_id: str = "me",
    type: int = 2,
    start_time: str = "",
    duration: int | None = None,
    password: str = "",
    agenda: str = "",
) -> Any:
    if not topic:
        raise ValueError("zoom_create_meeting_v2: topic is required")
    payload: dict[str, Any] = {"topic": topic, "type": type}
    if start_time:
        payload["start_time"] = start_time
    if duration is not None:
        payload["duration"] = duration
    if password:
        payload["password"] = password
    if agenda:
        payload["agenda"] = agenda
    return _transport(credentials).request(
        "POST",
        f"/users/{user_id}/meetings",
        operation="create_meeting",
        json_body=payload,
    )


def get_meeting(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    meeting_id: str = "",
) -> Any:
    if not meeting_id:
        raise ValueError("zoom_get_meeting_v2: meeting_id is required")
    return _transport(credentials).request(
        "GET",
        f"/meetings/{meeting_id}",
        operation="get_meeting",
    )


def list_meetings(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    user_id: str = "me",
    limit: int = 30,
    type: str = "scheduled",
) -> Any:
    return _transport(credentials).request(
        "GET",
        f"/users/{user_id}/meetings",
        operation="list_meetings",
        params={
            "page_size": max(1, min(300, int(limit or 30))),
            "type": type or "scheduled",
        },
    )


def delete_meeting(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    meeting_id: str = "",
) -> Any:
    if not meeting_id:
        raise ValueError("zoom_delete_meeting_v2: meeting_id is required")
    return _transport(credentials).request(
        "DELETE",
        f"/meetings/{meeting_id}",
        operation="delete_meeting",
    )


register_operation(ZOOM_CREATE_MEETING_SPEC, create_meeting, node_registry=None)
register_operation(ZOOM_GET_MEETING_SPEC, get_meeting, node_registry=None)
register_operation(ZOOM_LIST_MEETINGS_SPEC, list_meetings, node_registry=None)
register_operation(ZOOM_DELETE_MEETING_SPEC, delete_meeting, node_registry=None)

ZOOM_INTEGRATION = IntegrationSpec(
    id="zoom",
    name="Zoom",
    description="Create, get, list, and delete Zoom meetings.",
    icon="brand:zoom",
    credential_types=("zoom",),
    resources=(
        ResourceSpec(
            id="meeting",
            name="Meeting",
            operations=(
                ZOOM_CREATE_MEETING_SPEC,
                ZOOM_GET_MEETING_SPEC,
                ZOOM_LIST_MEETINGS_SPEC,
                ZOOM_DELETE_MEETING_SPEC,
            ),
        ),
    ),
)

register_integration(ZOOM_INTEGRATION)
