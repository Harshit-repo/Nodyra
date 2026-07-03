"""Jira v2 operation specs and executors."""

from __future__ import annotations

import json as json_mod
from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.errors import ProviderError
from nodyra_nodes.integrations_v2.registry import register_integration, register_operation
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from nodyra_nodes.integrations_v2.transport import ProviderTransport


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="jira",
            key="*",
            label="Jira credentials",
            fields=["email", "api_token"],
            multi=True,
            test_service="jira",
        ),
        description="Atlassian email + API token.",
    )


JIRA_CREATE_ISSUE_SPEC = OperationSpec(
    node_id="jira_create_issue_v2",
    name="Jira Create Issue",
    provider="jira",
    resource="issue",
    operation="create",
    description="Create an issue in Jira Cloud.",
    icon="brand:jira",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="site",
            required=True,
            placeholder="your-domain.atlassian.net",
            description="Jira Cloud site host (no scheme).",
        ),
        OperationParamSpec(
            name="project_key",
            required=True,
            placeholder="ENG",
        ),
        OperationParamSpec(
            name="summary",
            required=True,
        ),
        OperationParamSpec(
            name="description",
            group="Options",
            multiline=True,
        ),
        OperationParamSpec(
            name="issue_type",
            default="Task",
            group="Options",
            placeholder="Task",
            choices=("Task", "Bug", "Story", "Epic"),
        ),
        OperationParamSpec(
            name="priority",
            group="Options",
            placeholder="Medium",
            choices=("Highest", "High", "Medium", "Low", "Lowest"),
        ),
        OperationParamSpec(
            name="labels",
            type="array",
            group="Options",
        ),
    ),
)

JIRA_GET_ISSUE_SPEC = OperationSpec(
    node_id="jira_get_issue_v2",
    name="Jira Get Issue",
    provider="jira",
    resource="issue",
    operation="get",
    description="Get a Jira issue by key.",
    icon="brand:jira",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="site",
            required=True,
            placeholder="your-domain.atlassian.net",
        ),
        OperationParamSpec(
            name="issue_key",
            required=True,
            placeholder="ENG-123",
        ),
    ),
)

JIRA_UPDATE_ISSUE_SPEC = OperationSpec(
    node_id="jira_update_issue_v2",
    name="Jira Update Issue",
    provider="jira",
    resource="issue",
    operation="update",
    description="Update a Jira issue.",
    icon="brand:jira",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="site",
            required=True,
            placeholder="your-domain.atlassian.net",
        ),
        OperationParamSpec(
            name="issue_key",
            required=True,
            placeholder="ENG-123",
        ),
        OperationParamSpec(
            name="summary",
            group="Options",
        ),
        OperationParamSpec(
            name="description",
            group="Options",
            multiline=True,
        ),
        OperationParamSpec(
            name="status",
            group="Options",
            placeholder="In Progress",
        ),
        OperationParamSpec(
            name="priority",
            group="Options",
            placeholder="Medium",
            choices=("Highest", "High", "Medium", "Low", "Lowest"),
        ),
        OperationParamSpec(
            name="fields_json",
            group="Options",
            multiline=True,
            placeholder='{"customfield_10000": "value"}',
            description="Additional fields as JSON object.",
        ),
    ),
)

JIRA_DELETE_ISSUE_SPEC = OperationSpec(
    node_id="jira_delete_issue_v2",
    name="Jira Delete Issue",
    provider="jira",
    resource="issue",
    operation="delete",
    description="Delete a Jira issue.",
    icon="brand:jira",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="site",
            required=True,
            placeholder="your-domain.atlassian.net",
        ),
        OperationParamSpec(
            name="issue_key",
            required=True,
            placeholder="ENG-123",
        ),
    ),
)

JIRA_SEARCH_ISSUES_SPEC = OperationSpec(
    node_id="jira_search_issues_v2",
    name="Jira Search Issues",
    provider="jira",
    resource="issue",
    operation="search",
    description="Search Jira issues using JQL.",
    icon="brand:jira",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="site",
            required=True,
            placeholder="your-domain.atlassian.net",
        ),
        OperationParamSpec(
            name="jql",
            required=True,
            placeholder="project = ENG AND status = 'In Progress'",
        ),
        OperationParamSpec(
            name="max_results",
            type="number",
            default=50,
            group="Options",
        ),
        OperationParamSpec(
            name="fields",
            type="array",
            group="Options",
            placeholder='["summary", "status", "assignee"]',
            description="Fields to return. Blank returns all.",
        ),
    ),
)

JIRA_ADD_COMMENT_SPEC = OperationSpec(
    node_id="jira_add_comment_v2",
    name="Jira Add Comment",
    provider="jira",
    resource="issue",
    operation="add_comment",
    description="Add a comment to a Jira issue.",
    icon="brand:jira",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="site",
            required=True,
            placeholder="your-domain.atlassian.net",
        ),
        OperationParamSpec(
            name="issue_key",
            required=True,
            placeholder="ENG-123",
        ),
        OperationParamSpec(
            name="body",
            required=True,
            multiline=True,
            description="Comment body text.",
        ),
    ),
)

JIRA_LIST_PROJECTS_SPEC = OperationSpec(
    node_id="jira_list_projects_v2",
    name="Jira List Projects",
    provider="jira",
    resource="project",
    operation="list",
    description="List Jira projects.",
    icon="brand:jira",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="site",
            required=True,
            placeholder="your-domain.atlassian.net",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _transport(credentials: Any, site: str) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    email = str(creds.get("email") or "")
    api_token = str(creds.get("api_token") or "")
    if not email or not api_token:
        raise ValueError("jira: email and api_token are required")
    import base64

    token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return ProviderTransport(
        provider="jira",
        base_url=f"https://{site}",
        default_headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        },
    )


def _check_jira(response: Any, operation: str) -> Any:
    if isinstance(response, dict) and "errorMessages" in response:
        msgs = response["errorMessages"]
        if msgs:
            raise ProviderError(
                provider="jira",
                operation=operation,
                status_code=response.get("status", 400),
                code="jira_error",
                message="; ".join(str(m) for m in msgs),
                retryable=False,
                response_body_summary=str(response),
            )
    return response


def _adf_text(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _parse_json_field(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json_mod.loads(value)
        except json_mod.JSONDecodeError:
            pass
    return {}


def create_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    site: str = "",
    project_key: str = "",
    summary: str = "",
    description: str = "",
    issue_type: str = "Task",
    priority: str = "",
    labels: list[str] | None = None,
) -> Any:
    if not all([site, project_key, summary]):
        raise ValueError("jira_create_issue_v2: site, project_key, and summary are required")
    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type or "Task"},
    }
    if description:
        fields["description"] = _adf_text(description)
    if priority:
        fields["priority"] = {"name": priority}
    if labels:
        fields["labels"] = [str(label) for label in labels if str(label).strip()]
    result = _transport(credentials, site).request(
        "POST",
        "/rest/api/3/issue",
        operation="create_issue",
        json_body={"fields": fields},
    )
    return _check_jira(result, "create_issue")


def get_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    site: str = "",
    issue_key: str = "",
) -> Any:
    if not site or not issue_key:
        raise ValueError("jira_get_issue_v2: site and issue_key are required")
    result = _transport(credentials, site).request(
        "GET",
        f"/rest/api/3/issue/{issue_key}",
        operation="get_issue",
    )
    return _check_jira(result, "get_issue")


def update_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    site: str = "",
    issue_key: str = "",
    summary: str = "",
    description: str = "",
    status: str = "",
    priority: str = "",
    fields_json: str = "",
) -> Any:
    if not site or not issue_key:
        raise ValueError("jira_update_issue_v2: site and issue_key are required")
    fields: dict[str, Any] = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = _adf_text(description)
    if status:
        fields["status"] = {"name": status}
    if priority:
        fields["priority"] = {"name": priority}
    extra = _parse_json_field(fields_json)
    fields.update(extra)
    result = _transport(credentials, site).request(
        "PUT",
        f"/rest/api/3/issue/{issue_key}",
        operation="update_issue",
        json_body={"fields": fields},
    )
    return _check_jira(result, "update_issue")


def delete_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    site: str = "",
    issue_key: str = "",
) -> Any:
    if not site or not issue_key:
        raise ValueError("jira_delete_issue_v2: site and issue_key are required")
    result = _transport(credentials, site).request(
        "DELETE",
        f"/rest/api/3/issue/{issue_key}",
        operation="delete_issue",
    )
    return _check_jira(result, "delete_issue")


def search_issues(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    site: str = "",
    jql: str = "",
    max_results: int = 50,
    fields: list[str] | None = None,
) -> Any:
    if not site or not jql:
        raise ValueError("jira_search_issues_v2: site and jql are required")
    body: dict[str, Any] = {
        "jql": jql,
        "maxResults": max(1, min(100, int(max_results or 50))),
    }
    if fields:
        body["fields"] = list(fields)
    result = _transport(credentials, site).request(
        "POST",
        "/rest/api/3/search",
        operation="search_issues",
        json_body=body,
    )
    return _check_jira(result, "search_issues")


def add_comment(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    site: str = "",
    issue_key: str = "",
    body: str = "",
) -> Any:
    if not site or not issue_key or not body:
        raise ValueError("jira_add_comment_v2: site, issue_key, and body are required")
    result = _transport(credentials, site).request(
        "POST",
        f"/rest/api/3/issue/{issue_key}/comment",
        operation="add_comment",
        json_body={"body": _adf_text(body)},
    )
    return _check_jira(result, "add_comment")


def list_projects(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    site: str = "",
) -> Any:
    if not site:
        raise ValueError("jira_list_projects_v2: site is required")
    result = _transport(credentials, site).request(
        "GET",
        "/rest/api/3/project",
        operation="list_projects",
    )
    return _check_jira(result, "list_projects")


register_operation(JIRA_CREATE_ISSUE_SPEC, create_issue, node_registry=None)
register_operation(JIRA_GET_ISSUE_SPEC, get_issue, node_registry=None)
register_operation(JIRA_UPDATE_ISSUE_SPEC, update_issue, node_registry=None)
register_operation(JIRA_DELETE_ISSUE_SPEC, delete_issue, node_registry=None)
register_operation(JIRA_SEARCH_ISSUES_SPEC, search_issues, node_registry=None)
register_operation(JIRA_ADD_COMMENT_SPEC, add_comment, node_registry=None)
register_operation(JIRA_LIST_PROJECTS_SPEC, list_projects, node_registry=None)


JIRA_INTEGRATION = IntegrationSpec(
    id="jira",
    name="Jira",
    description="Create, update, search, and manage Jira issues and projects.",
    icon="brand:jira",
    credential_types=("jira",),
    resources=(
        ResourceSpec(
            id="issue",
            name="Issue",
            operations=(
                JIRA_CREATE_ISSUE_SPEC,
                JIRA_GET_ISSUE_SPEC,
                JIRA_UPDATE_ISSUE_SPEC,
                JIRA_DELETE_ISSUE_SPEC,
                JIRA_SEARCH_ISSUES_SPEC,
                JIRA_ADD_COMMENT_SPEC,
            ),
        ),
        ResourceSpec(
            id="project",
            name="Project",
            operations=(JIRA_LIST_PROJECTS_SPEC,),
        ),
    ),
)

register_integration(JIRA_INTEGRATION)
