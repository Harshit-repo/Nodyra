"""Asana v2 operation specs and executors."""

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

ASANA_API_BASE = "https://app.asana.com/api/1.0"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="asana",
            key="*",
            label="Asana personal access token",
            fields=["access_token"],
            multi=True,
            test_service="asana",
        ),
        description="Asana personal access token.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    token = str(creds.get("access_token") or "")
    if not token:
        raise ValueError("asana: access_token is required")
    return ProviderTransport(
        provider="asana",
        base_url=ASANA_API_BASE,
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


def _check(response: Any, operation: str) -> Any:
    if isinstance(response, dict) and "errors" in response:
        errors = response["errors"]
        msgs = [str(e.get("message", "")) for e in (errors or [])]
        raise ProviderError(
            provider="asana",
            operation=operation,
            status_code=response.get("status", 400),
            code="asana_error",
            message="; ".join(msgs) or "asana_api_error",
            retryable=False,
            response_body_summary=str(response),
        )
    return response


def _body_from_input(input_value: Any, body: str = "") -> str:
    if body:
        return body
    if input_value is None:
        return ""
    return str(input_value)


ASANA_CREATE_TASK_SPEC = OperationSpec(
    node_id="asana_create_task_v2",
    name="Asana Create Task",
    provider="asana",
    resource="task",
    operation="create",
    description="Create a task in Asana.",
    icon="brand:asana",
    params=(
        _credentials_param(),
        OperationParamSpec(name="name", required=True),
        OperationParamSpec(
            name="workspace_id",
            placeholder="workspace gid",
            description="Required if project is empty.",
        ),
        OperationParamSpec(name="project_id", placeholder="project gid"),
        OperationParamSpec(name="notes", group="Options", multiline=True),
        OperationParamSpec(name="assignee_id", group="Options"),
        OperationParamSpec(
            name="due_on",
            group="Options",
            placeholder="2025-12-31",
        ),
    ),
)

ASANA_GET_TASK_SPEC = OperationSpec(
    node_id="asana_get_task_v2",
    name="Asana Get Task",
    provider="asana",
    resource="task",
    operation="get",
    description="Get an Asana task by GID.",
    icon="brand:asana",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="task_gid", required=True, placeholder="1234567890"),
    ),
)

ASANA_UPDATE_TASK_SPEC = OperationSpec(
    node_id="asana_update_task_v2",
    name="Asana Update Task",
    provider="asana",
    resource="task",
    operation="update",
    description="Update an Asana task.",
    icon="brand:asana",
    params=(
        _credentials_param(),
        OperationParamSpec(name="task_gid", required=True),
        OperationParamSpec(name="name", group="Options"),
        OperationParamSpec(name="notes", group="Options", multiline=True),
        OperationParamSpec(name="completed", type="boolean", group="Options", default=None),
        OperationParamSpec(name="assignee_id", group="Options"),
    ),
)

ASANA_SEARCH_TASKS_SPEC = OperationSpec(
    node_id="asana_search_tasks_v2",
    name="Asana Search Tasks",
    provider="asana",
    resource="task",
    operation="search",
    description="Search tasks in an Asana project.",
    icon="brand:asana",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="project_id", required=True, placeholder="project gid"),
        OperationParamSpec(name="query", group="Options"),
        OperationParamSpec(name="completed", type="boolean", group="Options", default=None),
        OperationParamSpec(name="limit", type="number", default=50, group="Options"),
    ),
)

ASANA_LIST_PROJECTS_SPEC = OperationSpec(
    node_id="asana_list_projects_v2",
    name="Asana List Projects",
    provider="asana",
    resource="project",
    operation="list",
    description="List Asana projects.",
    icon="brand:asana",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="workspace_id", required=True),
    ),
)

ASANA_LIST_WORKSPACES_SPEC = OperationSpec(
    node_id="asana_list_workspaces_v2",
    name="Asana List Workspaces",
    provider="asana",
    resource="workspace",
    operation="list",
    description="List Asana workspaces / organizations.",
    icon="brand:asana",
    tool_side_effecting=False,
    params=(_credentials_param(),),
)

ASANA_ADD_COMMENT_SPEC = OperationSpec(
    node_id="asana_add_comment_v2",
    name="Asana Add Comment",
    provider="asana",
    resource="task",
    operation="add_comment",
    description="Add a comment to an Asana task.",
    icon="brand:asana",
    params=(
        _credentials_param(),
        OperationParamSpec(name="task_gid", required=True),
        OperationParamSpec(name="text", required=True, multiline=True),
    ),
)


def create_task(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    name: str = "",
    workspace_id: str = "",
    project_id: str = "",
    notes: str = "",
    assignee_id: str = "",
    due_on: str = "",
) -> Any:
    if not name:
        raise ValueError("asana_create_task_v2: name is required")
    data: dict[str, Any] = {"name": name}
    if workspace_id:
        data["workspace"] = workspace_id
    if project_id:
        data["projects"] = [project_id]
    if notes:
        data["notes"] = _body_from_input(input, notes)
    if assignee_id:
        data["assignee"] = assignee_id
    if due_on:
        data["due_on"] = due_on
    result = _transport(credentials).request(
        "POST",
        "/tasks",
        operation="create_task",
        json_body={"data": data},
    )
    return _check(result, "create_task")


def get_task(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    task_gid: str = "",
) -> Any:
    if not task_gid:
        raise ValueError("asana_get_task_v2: task_gid is required")
    result = _transport(credentials).request(
        "GET",
        f"/tasks/{task_gid}",
        operation="get_task",
    )
    return _check(result, "get_task")


def update_task(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    task_gid: str = "",
    name: str = "",
    notes: str = "",
    completed: bool | None = None,
    assignee_id: str = "",
) -> Any:
    if not task_gid:
        raise ValueError("asana_update_task_v2: task_gid is required")
    data: dict[str, Any] = {}
    if name:
        data["name"] = name
    if notes:
        data["notes"] = notes
    if completed is not None:
        data["completed"] = bool(completed)
    if assignee_id:
        data["assignee"] = assignee_id
    result = _transport(credentials).request(
        "PUT",
        f"/tasks/{task_gid}",
        operation="update_task",
        json_body={"data": data},
    )
    return _check(result, "update_task")


def search_tasks(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    project_id: str = "",
    query: str = "",
    completed: bool | None = None,
    limit: int = 50,
) -> Any:
    if not project_id:
        raise ValueError("asana_search_tasks_v2: project_id is required")
    params: dict[str, Any] = {
        "project": project_id,
        "limit": max(1, min(100, int(limit or 50))),
    }
    if query:
        params["text"] = query
    if completed is not None:
        params["completed_since"] = "now" if completed else "null"
    result = _transport(credentials).request(
        "GET",
        "/tasks",
        operation="search_tasks",
        params=params,
    )
    return _check(result, "search_tasks")


def list_projects(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    workspace_id: str = "",
) -> Any:
    if not workspace_id:
        raise ValueError("asana_list_projects_v2: workspace_id is required")
    result = _transport(credentials).request(
        "GET",
        "/projects",
        operation="list_projects",
        params={"workspace": workspace_id},
    )
    return _check(result, "list_projects")


def list_workspaces(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    result = _transport(credentials).request(
        "GET",
        "/workspaces",
        operation="list_workspaces",
    )
    return _check(result, "list_workspaces")


def add_comment(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    task_gid: str = "",
    text: str = "",
) -> Any:
    if not task_gid:
        raise ValueError("asana_add_comment_v2: task_gid is required")
    if not text:
        raise ValueError("asana_add_comment_v2: text is required")
    result = _transport(credentials).request(
        "POST",
        f"/tasks/{task_gid}/stories",
        operation="add_comment",
        json_body={"data": {"type": "comment", "text": _body_from_input(input, text)}},
    )
    return _check(result, "add_comment")


register_operation(ASANA_CREATE_TASK_SPEC, create_task, node_registry=None)
register_operation(ASANA_GET_TASK_SPEC, get_task, node_registry=None)
register_operation(ASANA_UPDATE_TASK_SPEC, update_task, node_registry=None)
register_operation(ASANA_SEARCH_TASKS_SPEC, search_tasks, node_registry=None)
register_operation(ASANA_LIST_PROJECTS_SPEC, list_projects, node_registry=None)
register_operation(ASANA_LIST_WORKSPACES_SPEC, list_workspaces, node_registry=None)
register_operation(ASANA_ADD_COMMENT_SPEC, add_comment, node_registry=None)


ASANA_INTEGRATION = IntegrationSpec(
    id="asana",
    name="Asana",
    description="Create, update, search, and manage Asana tasks, projects, and workspaces.",
    icon="brand:asana",
    credential_types=("asana",),
    resources=(
        ResourceSpec(
            id="task",
            name="Task",
            operations=(
                ASANA_CREATE_TASK_SPEC,
                ASANA_GET_TASK_SPEC,
                ASANA_UPDATE_TASK_SPEC,
                ASANA_SEARCH_TASKS_SPEC,
                ASANA_ADD_COMMENT_SPEC,
            ),
        ),
        ResourceSpec(
            id="project",
            name="Project",
            operations=(ASANA_LIST_PROJECTS_SPEC,),
        ),
        ResourceSpec(
            id="workspace",
            name="Workspace",
            operations=(ASANA_LIST_WORKSPACES_SPEC,),
        ),
    ),
)

register_integration(ASANA_INTEGRATION)
