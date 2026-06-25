"""GitLab v2 operation specs and executors."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_integration, register_operation
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport

GITLAB_API_DEFAULT_URL = "https://gitlab.com"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="gitlab_pat",
            key="*",
            label="GitLab Personal Access Token",
            fields=["server_url", "access_token"],
        ),
        description="GitLab personal access token with appropriate scopes.",
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    server_url = str(
        creds.get("server_url", GITLAB_API_DEFAULT_URL) or GITLAB_API_DEFAULT_URL
    ).rstrip("/")
    access_token = str(creds.get("access_token") or "")
    if not access_token:
        raise ValueError("gitlab: access_token is required")
    return ProviderTransport(
        provider="gitlab",
        base_url=server_url,
        default_headers={
            "PRIVATE-TOKEN": access_token,
            "Content-Type": "application/json",
        },
    )


GITLAB_LIST_PROJECTS_SPEC = OperationSpec(
    node_id="gitlab_list_projects_v2",
    name="GitLab List Projects",
    provider="gitlab",
    resource="project",
    operation="list",
    description="List GitLab projects accessible to the authenticated user.",
    icon="brand:gitlab",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="membership",
            type="boolean",
            default=True,
            description="Only show projects the user is a member of.",
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
        ),
        OperationParamSpec(
            name="search",
            group="Options",
            description="Search query for project name or path.",
        ),
    ),
)

GITLAB_GET_PROJECT_SPEC = OperationSpec(
    node_id="gitlab_get_project_v2",
    name="GitLab Get Project",
    provider="gitlab",
    resource="project",
    operation="get",
    description="Get details of a specific GitLab project.",
    icon="brand:gitlab",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="project_id",
            required=True,
            placeholder="12345 or namespace/project",
        ),
    ),
)

GITLAB_LIST_ISSUES_SPEC = OperationSpec(
    node_id="gitlab_list_issues_v2",
    name="GitLab List Issues",
    provider="gitlab",
    resource="issue",
    operation="list",
    description="List issues for a GitLab project.",
    icon="brand:gitlab",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="project_id", required=True),
        OperationParamSpec(
            name="state",
            choices=("opened", "closed", "all"),
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
        ),
    ),
)

GITLAB_CREATE_ISSUE_SPEC = OperationSpec(
    node_id="gitlab_create_issue_v2",
    name="GitLab Create Issue",
    provider="gitlab",
    resource="issue",
    operation="create",
    description="Create a new issue in a GitLab project.",
    icon="brand:gitlab",
    params=(
        _credentials_param(),
        OperationParamSpec(name="project_id", required=True),
        OperationParamSpec(name="title", required=True),
        OperationParamSpec(name="description", multiline=True, group="Options"),
        OperationParamSpec(
            name="assignee_id",
            type="number",
            group="Options",
        ),
        OperationParamSpec(
            name="labels",
            group="Options",
            description="Comma-separated label names.",
        ),
        OperationParamSpec(
            name="milestone_id",
            type="number",
            group="Options",
        ),
    ),
)

GITLAB_LIST_MERGE_REQUESTS_SPEC = OperationSpec(
    node_id="gitlab_list_merge_requests_v2",
    name="GitLab List Merge Requests",
    provider="gitlab",
    resource="merge_request",
    operation="list",
    description="List merge requests for a GitLab project.",
    icon="brand:gitlab",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="project_id", required=True),
        OperationParamSpec(
            name="state",
            choices=("opened", "closed", "merged", "all"),
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
        ),
    ),
)

GITLAB_CREATE_MERGE_REQUEST_SPEC = OperationSpec(
    node_id="gitlab_create_merge_request_v2",
    name="GitLab Create Merge Request",
    provider="gitlab",
    resource="merge_request",
    operation="create",
    description="Create a new merge request in a GitLab project.",
    icon="brand:gitlab",
    params=(
        _credentials_param(),
        OperationParamSpec(name="project_id", required=True),
        OperationParamSpec(name="source_branch", required=True),
        OperationParamSpec(name="target_branch", required=True),
        OperationParamSpec(name="title", required=True),
        OperationParamSpec(name="description", multiline=True, group="Options"),
    ),
)

GITLAB_GET_FILE_SPEC = OperationSpec(
    node_id="gitlab_get_file_v2",
    name="GitLab Get File",
    provider="gitlab",
    resource="file",
    operation="get",
    description="Get a single file from a GitLab repository.",
    icon="brand:gitlab",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="project_id", required=True),
        OperationParamSpec(
            name="file_path",
            required=True,
            placeholder="path/to/file.py",
        ),
        OperationParamSpec(
            name="branch",
            default="main",
            group="Options",
        ),
    ),
)


def list_projects(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    membership: bool = True,
    limit: int = 20,
    search: str = "",
) -> Any:
    params: dict[str, Any] = {
        "per_page": max(1, min(100, int(limit or 20))),
        "membership": str(membership).lower(),
    }
    if search:
        params["search"] = search
    return _transport(credentials).request(
        "GET",
        "/api/v4/projects",
        operation="list_projects",
        params=params,
    )


def get_project(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    project_id: str = "",
) -> Any:
    if not project_id:
        raise ValueError("gitlab_get_project_v2: project_id is required")
    return _transport(credentials).request(
        "GET",
        f"/api/v4/projects/{quote(str(project_id), safe='')}",
        operation="get_project",
    )


def list_issues(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    project_id: str = "",
    state: str = "",
    limit: int = 20,
) -> Any:
    if not project_id:
        raise ValueError("gitlab_list_issues_v2: project_id is required")
    params: dict[str, Any] = {
        "per_page": max(1, min(100, int(limit or 20))),
    }
    if state:
        params["state"] = state
    return _transport(credentials).request(
        "GET",
        f"/api/v4/projects/{quote(str(project_id), safe='')}/issues",
        operation="list_issues",
        params=params,
    )


def create_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    project_id: str = "",
    title: str = "",
    description: str = "",
    assignee_id: int | None = None,
    labels: str = "",
    milestone_id: int | None = None,
) -> Any:
    if not project_id:
        raise ValueError("gitlab_create_issue_v2: project_id is required")
    if not title:
        raise ValueError("gitlab_create_issue_v2: title is required")
    payload: dict[str, Any] = {"title": title}
    if description:
        payload["description"] = description
    if assignee_id is not None:
        payload["assignee_id"] = int(assignee_id)
    if labels:
        payload["labels"] = labels
    if milestone_id is not None:
        payload["milestone_id"] = int(milestone_id)
    return _transport(credentials).request(
        "POST",
        f"/api/v4/projects/{quote(str(project_id), safe='')}/issues",
        operation="create_issue",
        json_body=payload,
    )


def list_merge_requests(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    project_id: str = "",
    state: str = "",
    limit: int = 20,
) -> Any:
    if not project_id:
        raise ValueError("gitlab_list_merge_requests_v2: project_id is required")
    params: dict[str, Any] = {
        "per_page": max(1, min(100, int(limit or 20))),
    }
    if state:
        params["state"] = state
    return _transport(credentials).request(
        "GET",
        f"/api/v4/projects/{quote(str(project_id), safe='')}/merge_requests",
        operation="list_merge_requests",
        params=params,
    )


def create_merge_request(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    project_id: str = "",
    source_branch: str = "",
    target_branch: str = "",
    title: str = "",
    description: str = "",
) -> Any:
    if not project_id:
        raise ValueError("gitlab_create_merge_request_v2: project_id is required")
    if not source_branch:
        raise ValueError("gitlab_create_merge_request_v2: source_branch is required")
    if not target_branch:
        raise ValueError("gitlab_create_merge_request_v2: target_branch is required")
    if not title:
        raise ValueError("gitlab_create_merge_request_v2: title is required")
    payload: dict[str, Any] = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": title,
    }
    if description:
        payload["description"] = description
    return _transport(credentials).request(
        "POST",
        f"/api/v4/projects/{quote(str(project_id), safe='')}/merge_requests",
        operation="create_merge_request",
        json_body=payload,
    )


def get_file(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    project_id: str = "",
    file_path: str = "",
    branch: str = "main",
) -> Any:
    if not project_id:
        raise ValueError("gitlab_get_file_v2: project_id is required")
    if not file_path:
        raise ValueError("gitlab_get_file_v2: file_path is required")
    encoded_path = base64.b64encode(file_path.encode()).decode()
    params: dict[str, Any] = {"ref": branch or "main"}
    return _transport(credentials).request(
        "GET",
        f"/api/v4/projects/{quote(str(project_id), safe='')}/repository/files/{quote(encoded_path, safe='')}",
        operation="get_file",
        params=params,
    )


register_operation(GITLAB_LIST_PROJECTS_SPEC, list_projects, node_registry=None)
register_operation(GITLAB_GET_PROJECT_SPEC, get_project, node_registry=None)
register_operation(GITLAB_LIST_ISSUES_SPEC, list_issues, node_registry=None)
register_operation(GITLAB_CREATE_ISSUE_SPEC, create_issue, node_registry=None)
register_operation(GITLAB_LIST_MERGE_REQUESTS_SPEC, list_merge_requests, node_registry=None)
register_operation(GITLAB_CREATE_MERGE_REQUEST_SPEC, create_merge_request, node_registry=None)
register_operation(GITLAB_GET_FILE_SPEC, get_file, node_registry=None)


GITLAB_INTEGRATION = IntegrationSpec(
    id="gitlab",
    name="GitLab",
    description="Manage GitLab projects, issues, merge requests, and repository files.",
    icon="brand:gitlab",
    credential_types=("gitlab_pat",),
    resources=(
        ResourceSpec(
            id="project",
            name="Project",
            operations=(
                GITLAB_LIST_PROJECTS_SPEC,
                GITLAB_GET_PROJECT_SPEC,
            ),
        ),
        ResourceSpec(
            id="issue",
            name="Issue",
            operations=(
                GITLAB_LIST_ISSUES_SPEC,
                GITLAB_CREATE_ISSUE_SPEC,
            ),
        ),
        ResourceSpec(
            id="merge_request",
            name="Merge Request",
            operations=(
                GITLAB_LIST_MERGE_REQUESTS_SPEC,
                GITLAB_CREATE_MERGE_REQUEST_SPEC,
            ),
        ),
        ResourceSpec(
            id="file",
            name="File",
            operations=(GITLAB_GET_FILE_SPEC,),
        ),
    ),
)

register_integration(GITLAB_INTEGRATION)
