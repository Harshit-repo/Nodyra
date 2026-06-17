"""Linear v2 operation specs and executors."""

from __future__ import annotations

import json as json_mod
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

LINEAR_API_BASE = "https://api.linear.app/graphql"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="linear",
            key="*",
            label="Linear API key",
            fields=["api_key"],
            multi=True,
            test_service="linear",
        ),
        description="Linear personal API key.",
    )


LINEAR_CREATE_ISSUE_SPEC = OperationSpec(
    node_id="linear_create_issue_v2",
    name="Linear Create Issue",
    provider="linear",
    resource="issue",
    operation="create",
    description="Create an issue in Linear.",
    icon="brand:linear",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="team_id",
            required=True,
            placeholder="team UUID",
        ),
        OperationParamSpec(
            name="title",
            required=True,
        ),
        OperationParamSpec(
            name="description",
            group="Options",
            multiline=True,
        ),
        OperationParamSpec(
            name="priority",
            group="Options",
            default="0",
            choices=("0", "1", "2", "3", "4"),
            description="0=none, 1=urgent, 2=high, 3=medium, 4=low.",
        ),
        OperationParamSpec(
            name="assignee_id",
            group="Options",
            placeholder="user UUID",
        ),
        OperationParamSpec(
            name="labels",
            type="array",
            group="Options",
            description="Label IDs to attach.",
        ),
    ),
)

LINEAR_GET_ISSUE_SPEC = OperationSpec(
    node_id="linear_get_issue_v2",
    name="Linear Get Issue",
    provider="linear",
    resource="issue",
    operation="get",
    description="Get a Linear issue by ID.",
    icon="brand:linear",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="issue_id",
            required=True,
            placeholder="issue UUID",
        ),
    ),
)

LINEAR_UPDATE_ISSUE_SPEC = OperationSpec(
    node_id="linear_update_issue_v2",
    name="Linear Update Issue",
    provider="linear",
    resource="issue",
    operation="update",
    description="Update a Linear issue.",
    icon="brand:linear",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="issue_id",
            required=True,
            placeholder="issue UUID",
        ),
        OperationParamSpec(name="title", group="Options"),
        OperationParamSpec(
            name="description",
            group="Options",
            multiline=True,
        ),
        OperationParamSpec(
            name="priority",
            group="Options",
            default="0",
            choices=("0", "1", "2", "3", "4"),
        ),
        OperationParamSpec(
            name="completed",
            type="boolean",
            group="Options",
            default=None,
        ),
    ),
)

LINEAR_SEARCH_ISSUES_SPEC = OperationSpec(
    node_id="linear_search_issues_v2",
    name="Linear Search Issues",
    provider="linear",
    resource="issue",
    operation="search",
    description="Search Linear issues.",
    icon="brand:linear",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="filter",
            group="Options",
            placeholder='{"team": {"id": {"eq": "team-uuid"}}}',
            description="GraphQL filter JSON.",
        ),
        OperationParamSpec(
            name="first",
            type="number",
            default=50,
            group="Options",
        ),
        OperationParamSpec(
            name="include_completed",
            type="boolean",
            default=False,
            group="Options",
        ),
    ),
)

LINEAR_LIST_TEAMS_SPEC = OperationSpec(
    node_id="linear_list_teams_v2",
    name="Linear List Teams",
    provider="linear",
    resource="team",
    operation="list",
    description="List Linear teams.",
    icon="brand:linear",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
    ),
)

LINEAR_ADD_COMMENT_SPEC = OperationSpec(
    node_id="linear_add_comment_v2",
    name="Linear Add Comment",
    provider="linear",
    resource="issue",
    operation="add_comment",
    description="Add a comment to a Linear issue.",
    icon="brand:linear",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="issue_id",
            required=True,
            placeholder="issue UUID",
        ),
        OperationParamSpec(
            name="body",
            required=True,
            multiline=True,
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"api_key": value}
    return {}


def _api_key(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("api_key") or creds.get("token") or "")


def _transport(credentials: Any) -> ProviderTransport:
    key = _api_key(credentials)
    if not key:
        raise ValueError("linear: api_key is required")
    return ProviderTransport(
        provider="linear",
        base_url=LINEAR_API_BASE,
        default_headers={
            "Authorization": key,
            "Content-Type": "application/json",
        },
    )


def _check_linear(response: Any, operation: str) -> Any:
    if isinstance(response, dict):
        errors = response.get("errors")
        if errors:
            messages = [str(e.get("message", "")) for e in (errors or [])]
            raise ProviderError(
                provider="linear",
                operation=operation,
                status_code=200,
                code="linear_error",
                message="; ".join(messages) or "linear_graphql_error",
                retryable=False,
                response_body_summary=str(response),
            )
    return response


def _graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query}
    if variables:
        body["variables"] = variables
    return body


def create_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    team_id: str = "",
    title: str = "",
    description: str = "",
    priority: str = "0",
    assignee_id: str = "",
    labels: list[str] | None = None,
) -> Any:
    if not team_id or not title:
        raise ValueError("linear_create_issue_v2: team_id and title are required")
    variables: dict[str, Any] = {
        "input": {
            "teamId": team_id,
            "title": title,
            "description": description or "",
            "priority": int(priority or "0"),
        }
    }
    if assignee_id:
        variables["input"]["assigneeId"] = assignee_id
    if labels:
        variables["input"]["labelIds"] = [str(l) for l in labels if str(l).strip()]
    result = _transport(credentials).request(
        "POST",
        "",
        operation="create_issue",
        json_body=_graphql(
            """
            mutation($input: IssueCreateInput!) {
                issueCreate(input: $input) {
                    success
                    issue { id identifier url title }
                }
            }
            """,
            variables,
        ),
    )
    return _check_linear(result, "create_issue")


def get_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    issue_id: str = "",
) -> Any:
    if not issue_id:
        raise ValueError("linear_get_issue_v2: issue_id is required")
    result = _transport(credentials).request(
        "POST",
        "",
        operation="get_issue",
        json_body=_graphql(
            """
            query($id: String!) {
                issue(id: $id) {
                    id identifier title description priority
                    completed createdAt updatedAt
                    team { id name }
                    assignee { id name email }
                }
            }
            """,
            {"id": issue_id},
        ),
    )
    return _check_linear(result, "get_issue")


def update_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    issue_id: str = "",
    title: str = "",
    description: str = "",
    priority: str = "0",
    completed: bool | None = None,
) -> Any:
    if not issue_id:
        raise ValueError("linear_update_issue_v2: issue_id is required")
    variables: dict[str, Any] = {
        "input": {
            "id": issue_id,
        }
    }
    if title:
        variables["input"]["title"] = title
    if description:
        variables["input"]["description"] = description
    if priority:
        variables["input"]["priority"] = int(priority)
    if completed is not None:
        variables["input"]["completed"] = bool(completed)
    result = _transport(credentials).request(
        "POST",
        "",
        operation="update_issue",
        json_body=_graphql(
            """
            mutation($input: IssueUpdateInput!) {
                issueUpdate(input: $input) {
                    success
                    issue { id identifier title priority completed }
                }
            }
            """,
            variables,
        ),
    )
    return _check_linear(result, "update_issue")


def search_issues(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    filter: str = "",
    first: int = 50,
    include_completed: bool = False,
) -> Any:
    filter_obj: dict[str, Any] = {}
    if filter and isinstance(filter, str) and filter.strip():
        try:
            filter_obj = json_mod.loads(filter)
        except json_mod.JSONDecodeError:
            filter_obj = {}
    if not include_completed:
        filter_obj.setdefault("completed", {}).setdefault("eq", False)
    result = _transport(credentials).request(
        "POST",
        "",
        operation="search_issues",
        json_body=_graphql(
            """
            query($filter: IssueFilter, $first: Int!) {
                issues(filter: $filter, first: $first) {
                    nodes {
                        id identifier title description priority
                        completed createdAt updatedAt
                        team { id name }
                        assignee { id name email }
                    }
                }
            }
            """,
            {"filter": filter_obj, "first": max(1, min(250, int(first or 50)))},
        ),
    )
    return _check_linear(result, "search_issues")


def list_teams(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    result = _transport(credentials).request(
        "POST",
        "",
        operation="list_teams",
        json_body=_graphql(
            """
            query {
                teams {
                    nodes { id name key description }
                }
            }
            """,
        ),
    )
    return _check_linear(result, "list_teams")


def add_comment(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    issue_id: str = "",
    body: str = "",
) -> Any:
    if not issue_id or not body:
        raise ValueError("linear_add_comment_v2: issue_id and body are required")
    result = _transport(credentials).request(
        "POST",
        "",
        operation="add_comment",
        json_body=_graphql(
            """
            mutation($input: CommentCreateInput!) {
                commentCreate(input: $input) {
                    success
                    comment { id body }
                }
            }
            """,
            {"input": {"issueId": issue_id, "body": body}},
        ),
    )
    return _check_linear(result, "add_comment")


register_operation(LINEAR_CREATE_ISSUE_SPEC, create_issue, node_registry=None)
register_operation(LINEAR_GET_ISSUE_SPEC, get_issue, node_registry=None)
register_operation(LINEAR_UPDATE_ISSUE_SPEC, update_issue, node_registry=None)
register_operation(LINEAR_SEARCH_ISSUES_SPEC, search_issues, node_registry=None)
register_operation(LINEAR_LIST_TEAMS_SPEC, list_teams, node_registry=None)
register_operation(LINEAR_ADD_COMMENT_SPEC, add_comment, node_registry=None)


LINEAR_INTEGRATION = IntegrationSpec(
    id="linear",
    name="Linear",
    description="Create, update, search, and manage Linear issues and teams.",
    icon="brand:linear",
    credential_types=("linear",),
    resources=(
        ResourceSpec(
            id="issue",
            name="Issue",
            operations=(
                LINEAR_CREATE_ISSUE_SPEC,
                LINEAR_GET_ISSUE_SPEC,
                LINEAR_UPDATE_ISSUE_SPEC,
                LINEAR_SEARCH_ISSUES_SPEC,
                LINEAR_ADD_COMMENT_SPEC,
            ),
        ),
        ResourceSpec(
            id="team",
            name="Team",
            operations=(LINEAR_LIST_TEAMS_SPEC,),
        ),
    ),
)

register_integration(LINEAR_INTEGRATION)
