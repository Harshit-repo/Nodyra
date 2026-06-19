"""ClickUp v2 operation specs and executors."""

from __future__ import annotations

import json
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

CLICKUP_API_BASE = "https://api.clickup.com/api/v2"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="clickup",
            key="*",
            label="ClickUp API token",
            fields=["access_token"],
            multi=True,
        ),
        description="ClickUp API token.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    token = str(creds.get("access_token") or "")
    if not token:
        raise ValueError("clickup: access_token is required")
    return ProviderTransport(
        provider="clickup",
        base_url=CLICKUP_API_BASE,
        default_headers={
            "Authorization": token,
            "Content-Type": "application/json",
        },
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"access_token": value}
    return {}


def _text_from_input(input_value: Any, text: str = "") -> str:
    if text:
        return text
    if input_value is None:
        return ""
    return str(input_value)


def _parse_json(value: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


CLICKUP_LIST_TEAMS_SPEC = OperationSpec(
    node_id="clickup_list_teams_v2",
    name="ClickUp List Teams",
    provider="clickup",
    resource="team",
    operation="list",
    description="List all teams/workspaces accessible to the authenticated user.",
    icon="brand:clickup",
    tool_side_effecting=False,
    params=(_credentials_param(),),
)

CLICKUP_LIST_SPACES_SPEC = OperationSpec(
    node_id="clickup_list_spaces_v2",
    name="ClickUp List Spaces",
    provider="clickup",
    resource="space",
    operation="list",
    description="List all spaces in a team.",
    icon="brand:clickup",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="team_id", required=True, placeholder="12345"),
    ),
)

CLICKUP_LIST_FOLDERS_SPEC = OperationSpec(
    node_id="clickup_list_folders_v2",
    name="ClickUp List Folders",
    provider="clickup",
    resource="folder",
    operation="list",
    description="List all folders in a space.",
    icon="brand:clickup",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="space_id", required=True, placeholder="12345"),
    ),
)

CLICKUP_LIST_LISTS_SPEC = OperationSpec(
    node_id="clickup_list_lists_v2",
    name="ClickUp List Lists",
    provider="clickup",
    resource="list",
    operation="list",
    description="List all lists in a folder.",
    icon="brand:clickup",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="folder_id", required=True, placeholder="12345"),
    ),
)

CLICKUP_CREATE_TASK_SPEC = OperationSpec(
    node_id="clickup_create_task_v2",
    name="ClickUp Create Task",
    provider="clickup",
    resource="task",
    operation="create",
    description="Create a new task in a ClickUp list.",
    icon="brand:clickup",
    params=(
        _credentials_param(),
        OperationParamSpec(name="list_id", required=True, placeholder="12345"),
        OperationParamSpec(name="name", required=True, placeholder="Task name"),
        OperationParamSpec(
            name="description",
            multiline=True,
            group="Options",
        ),
        OperationParamSpec(
            name="priority",
            type="number",
            group="Options",
            choices=(1, 2, 3, 4),
        ),
        OperationParamSpec(
            name="due_date",
            type="number",
            group="Options",
            placeholder="Milliseconds epoch",
        ),
        OperationParamSpec(
            name="assignees",
            type="json",
            group="Options",
            placeholder='[183, 184]',
        ),
        OperationParamSpec(
            name="tags",
            group="Options",
            placeholder="comma-separated",
        ),
    ),
)

CLICKUP_GET_TASK_SPEC = OperationSpec(
    node_id="clickup_get_task_v2",
    name="ClickUp Get Task",
    provider="clickup",
    resource="task",
    operation="get",
    description="Get details of a specific task by ID.",
    icon="brand:clickup",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="task_id", required=True, placeholder="abc123"),
    ),
)

CLICKUP_UPDATE_TASK_SPEC = OperationSpec(
    node_id="clickup_update_task_v2",
    name="ClickUp Update Task",
    provider="clickup",
    resource="task",
    operation="update",
    description="Update an existing ClickUp task.",
    icon="brand:clickup",
    params=(
        _credentials_param(),
        OperationParamSpec(name="task_id", required=True, placeholder="abc123"),
        OperationParamSpec(name="name", group="Options"),
        OperationParamSpec(
            name="description",
            multiline=True,
            group="Options",
        ),
        OperationParamSpec(
            name="priority",
            type="number",
            group="Options",
            choices=(1, 2, 3, 4),
        ),
        OperationParamSpec(name="status", group="Options"),
        OperationParamSpec(
            name="due_date",
            type="number",
            group="Options",
            placeholder="Milliseconds epoch",
        ),
        OperationParamSpec(
            name="assignees",
            type="json",
            group="Options",
            placeholder='{"add": [183], "rem": []}',
        ),
    ),
)


def list_teams(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/team",
        operation="list_teams",
    )


def list_spaces(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    team_id: str = "",
) -> Any:
    if not team_id:
        raise ValueError("clickup_list_spaces_v2: team_id is required")
    return _transport(credentials).request(
        "GET",
        f"/team/{team_id}/space",
        operation="list_spaces",
    )


def list_folders(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    space_id: str = "",
) -> Any:
    if not space_id:
        raise ValueError("clickup_list_folders_v2: space_id is required")
    return _transport(credentials).request(
        "GET",
        f"/space/{space_id}/folder",
        operation="list_folders",
    )


def list_lists(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    folder_id: str = "",
) -> Any:
    if not folder_id:
        raise ValueError("clickup_list_lists_v2: folder_id is required")
    return _transport(credentials).request(
        "GET",
        f"/folder/{folder_id}/list",
        operation="list_lists",
    )


def create_task(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    list_id: str = "",
    name: str = "",
    description: str = "",
    priority: int | None = None,
    due_date: int | None = None,
    assignees: Any = None,
    tags: str = "",
) -> Any:
    if not list_id:
        raise ValueError("clickup_create_task_v2: list_id is required")
    if not name:
        raise ValueError("clickup_create_task_v2: name is required")
    body: dict[str, Any] = {"name": name}
    if description:
        body["description"] = description
    if priority is not None:
        body["priority"] = int(priority)
    if due_date is not None:
        body["due_date"] = int(due_date)
    if assignees:
        parsed = _parse_json(assignees)
        if isinstance(parsed, list):
            body["assignees"] = parsed
    if tags:
        body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    return _transport(credentials).request(
        "POST",
        f"/list/{list_id}/task",
        operation="create_task",
        json_body=body,
    )


def get_task(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    task_id: str = "",
) -> Any:
    if not task_id:
        raise ValueError("clickup_get_task_v2: task_id is required")
    return _transport(credentials).request(
        "GET",
        f"/task/{task_id}",
        operation="get_task",
    )


def update_task(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    task_id: str = "",
    name: str = "",
    description: str = "",
    priority: int | None = None,
    status: str = "",
    due_date: int | None = None,
    assignees: Any = None,
) -> Any:
    if not task_id:
        raise ValueError("clickup_update_task_v2: task_id is required")
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if description:
        body["description"] = description
    if priority is not None:
        body["priority"] = int(priority)
    if status:
        body["status"] = status
    if due_date is not None:
        body["due_date"] = int(due_date)
    if assignees:
        parsed = _parse_json(assignees)
        if isinstance(parsed, dict):
            body["assignees"] = parsed
    return _transport(credentials).request(
        "PUT",
        f"/task/{task_id}",
        operation="update_task",
        json_body=body,
    )


register_operation(CLICKUP_LIST_TEAMS_SPEC, list_teams, node_registry=None)
register_operation(CLICKUP_LIST_SPACES_SPEC, list_spaces, node_registry=None)
register_operation(CLICKUP_LIST_FOLDERS_SPEC, list_folders, node_registry=None)
register_operation(CLICKUP_LIST_LISTS_SPEC, list_lists, node_registry=None)
register_operation(CLICKUP_CREATE_TASK_SPEC, create_task, node_registry=None)
register_operation(CLICKUP_GET_TASK_SPEC, get_task, node_registry=None)
register_operation(CLICKUP_UPDATE_TASK_SPEC, update_task, node_registry=None)

CLICKUP_INTEGRATION = IntegrationSpec(
    id="clickup",
    name="ClickUp",
    description="Create, read, and manage ClickUp tasks, lists, folders, spaces, and teams.",
    icon="brand:clickup",
    credential_types=("clickup",),
    resources=(
        ResourceSpec(
            id="team",
            name="Team",
            operations=(CLICKUP_LIST_TEAMS_SPEC,),
        ),
        ResourceSpec(
            id="space",
            name="Space",
            operations=(CLICKUP_LIST_SPACES_SPEC,),
        ),
        ResourceSpec(
            id="folder",
            name="Folder",
            operations=(CLICKUP_LIST_FOLDERS_SPEC,),
        ),
        ResourceSpec(
            id="list",
            name="List",
            operations=(CLICKUP_LIST_LISTS_SPEC,),
        ),
        ResourceSpec(
            id="task",
            name="Task",
            operations=(
                CLICKUP_CREATE_TASK_SPEC,
                CLICKUP_GET_TASK_SPEC,
                CLICKUP_UPDATE_TASK_SPEC,
            ),
        ),
    ),
)

register_integration(CLICKUP_INTEGRATION)
