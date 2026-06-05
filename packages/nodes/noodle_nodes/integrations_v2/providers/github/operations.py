"""GitHub v2 operation specs and executors."""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import quote

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_operation
from noodle_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from noodle_nodes.integrations_v2.transport import ProviderTransport

GITHUB_API_BASE = "https://api.github.com"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="github",
            key="*",
            label="GitHub token",
            fields=["token"],
            multi=True,
            test_service="github",
        ),
        description="GitHub token.",
    )


GITHUB_GET_REPO_SPEC = OperationSpec(
    node_id="github_get_repo_v2",
    name="GitHub Get Repository",
    provider="github",
    resource="repository",
    operation="get",
    description="Fetch GitHub repository metadata using the v2 provider transport.",
    icon="brand:github",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="repo",
            required=True,
            placeholder="owner/name",
            description="Repository path in owner/name format.",
        ),
    ),
)


GITHUB_CREATE_ISSUE_SPEC = OperationSpec(
    node_id="github_create_issue_v2",
    name="GitHub Create Issue",
    provider="github",
    resource="issue",
    operation="create",
    description="Create a GitHub issue using the v2 provider transport.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="repo",
            required=True,
            placeholder="owner/name",
            description="Repository path in owner/name format.",
        ),
        OperationParamSpec(
            name="title",
            required=True,
            placeholder="Issue title",
        ),
        OperationParamSpec(
            name="body",
            multiline=True,
            group="Options",
            description="Issue body. Blank uses the input payload.",
        ),
        OperationParamSpec(
            name="labels",
            type="array",
            group="Options",
            description="Optional labels as an array or comma-separated string.",
        ),
    ),
)

GITHUB_LIST_ISSUES_SPEC = OperationSpec(
    node_id="github_list_issues_v2",
    name="GitHub List Issues",
    provider="github",
    resource="issue",
    operation="list",
    description="List issues in a repository.",
    icon="brand:github",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="repo",
            required=True,
            placeholder="owner/name",
            description="Repository path in owner/name format.",
        ),
        OperationParamSpec(
            name="state",
            default="open",
            choices=("open", "closed", "all"),
            group="Filters",
        ),
        OperationParamSpec(
            name="labels",
            type="array",
            group="Filters",
            description="Labels as an array or comma-separated string.",
        ),
        OperationParamSpec(name="assignee", group="Filters"),
        OperationParamSpec(name="since", group="Filters", placeholder="2024-01-01T00:00:00Z"),
        OperationParamSpec(name="per_page", type="number", default=30, group="Options"),
    ),
)

GITHUB_GET_ISSUE_SPEC = OperationSpec(
    node_id="github_get_issue_v2",
    name="GitHub Get Issue",
    provider="github",
    resource="issue",
    operation="get",
    description="Get a single GitHub issue by number.",
    icon="brand:github",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="issue_number", type="number", required=True),
    ),
)

GITHUB_UPDATE_ISSUE_SPEC = OperationSpec(
    node_id="github_update_issue_v2",
    name="GitHub Update Issue",
    provider="github",
    resource="issue",
    operation="update",
    description="Update title, body, state, labels, assignees, or milestone for an issue.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="issue_number", type="number", required=True),
        OperationParamSpec(name="title", group="Fields"),
        OperationParamSpec(name="body", multiline=True, group="Fields"),
        OperationParamSpec(
            name="state",
            choices=("open", "closed"),
            group="Fields",
        ),
        OperationParamSpec(
            name="state_reason",
            choices=("completed", "not_planned", "reopened"),
            group="Fields",
        ),
        OperationParamSpec(name="labels", type="array", group="Fields"),
        OperationParamSpec(name="assignees", type="array", group="Fields"),
        OperationParamSpec(name="milestone", type="number", default=None, group="Fields"),
    ),
)

GITHUB_CREATE_ISSUE_COMMENT_SPEC = OperationSpec(
    node_id="github_create_issue_comment_v2",
    name="GitHub Create Issue Comment",
    provider="github",
    resource="issue_comment",
    operation="create",
    description="Add a comment to an issue or pull request.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="issue_number", type="number", required=True),
        OperationParamSpec(name="body", multiline=True, description="Blank uses input."),
    ),
)

GITHUB_LIST_ISSUE_COMMENTS_SPEC = OperationSpec(
    node_id="github_list_issue_comments_v2",
    name="GitHub List Issue Comments",
    provider="github",
    resource="issue_comment",
    operation="list",
    description="List comments on a GitHub issue or pull request.",
    icon="brand:github",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="issue_number", type="number", required=True),
        OperationParamSpec(name="since", group="Filters", placeholder="2024-01-01T00:00:00Z"),
        OperationParamSpec(name="per_page", type="number", default=30, group="Options"),
    ),
)

GITHUB_LIST_PULL_REQUESTS_SPEC = OperationSpec(
    node_id="github_list_pull_requests_v2",
    name="GitHub List Pull Requests",
    provider="github",
    resource="pull_request",
    operation="list",
    description="List pull requests in a repository.",
    icon="brand:github",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(
            name="state",
            default="open",
            choices=("open", "closed", "all"),
            group="Filters",
        ),
        OperationParamSpec(name="head", group="Filters", placeholder="user:branch"),
        OperationParamSpec(name="base", group="Filters", placeholder="main"),
        OperationParamSpec(
            name="sort",
            default="created",
            choices=("created", "updated", "popularity", "long-running"),
            group="Options",
        ),
        OperationParamSpec(
            name="direction",
            default="desc",
            choices=("asc", "desc"),
            group="Options",
        ),
        OperationParamSpec(name="per_page", type="number", default=30, group="Options"),
    ),
)

GITHUB_GET_PULL_REQUEST_SPEC = OperationSpec(
    node_id="github_get_pull_request_v2",
    name="GitHub Get Pull Request",
    provider="github",
    resource="pull_request",
    operation="get",
    description="Get a pull request by number.",
    icon="brand:github",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="pull_number", type="number", required=True),
    ),
)

GITHUB_CREATE_PULL_REQUEST_SPEC = OperationSpec(
    node_id="github_create_pull_request_v2",
    name="GitHub Create Pull Request",
    provider="github",
    resource="pull_request",
    operation="create",
    description="Create a pull request.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="title", required=True),
        OperationParamSpec(name="head", required=True, placeholder="feature-branch"),
        OperationParamSpec(name="base", required=True, placeholder="main"),
        OperationParamSpec(name="body", multiline=True, group="Options"),
        OperationParamSpec(name="draft", type="boolean", default=False, group="Options"),
        OperationParamSpec(
            name="maintainer_can_modify",
            type="boolean",
            default=True,
            group="Options",
        ),
    ),
)

GITHUB_MERGE_PULL_REQUEST_SPEC = OperationSpec(
    node_id="github_merge_pull_request_v2",
    name="GitHub Merge Pull Request",
    provider="github",
    resource="pull_request",
    operation="merge",
    description="Merge a pull request.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="pull_number", type="number", required=True),
        OperationParamSpec(name="commit_title", group="Options"),
        OperationParamSpec(name="commit_message", multiline=True, group="Options"),
        OperationParamSpec(
            name="merge_method",
            default="merge",
            choices=("merge", "squash", "rebase"),
            group="Options",
        ),
    ),
)

GITHUB_GET_FILE_CONTENTS_SPEC = OperationSpec(
    node_id="github_get_file_contents_v2",
    name="GitHub Get File Contents",
    provider="github",
    resource="contents",
    operation="get",
    description="Get repository file contents or directory metadata.",
    icon="brand:github",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="path", required=True, placeholder="README.md"),
        OperationParamSpec(name="ref", group="Options", placeholder="main"),
    ),
)

GITHUB_PUT_FILE_CONTENTS_SPEC = OperationSpec(
    node_id="github_put_file_contents_v2",
    name="GitHub Create Or Update File",
    provider="github",
    resource="contents",
    operation="put",
    description="Create or update a repository file.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="path", required=True, placeholder="docs/report.md"),
        OperationParamSpec(name="message", required=True, placeholder="Update report"),
        OperationParamSpec(name="content", multiline=True, description="Plain text content."),
        OperationParamSpec(
            name="content_base64",
            group="Options",
            description="Pre-encoded base64 content. Overrides content when set.",
        ),
        OperationParamSpec(name="branch", group="Options", placeholder="main"),
        OperationParamSpec(
            name="sha",
            group="Options",
            description="Existing file SHA. Set this when updating a file.",
        ),
        OperationParamSpec(name="committer", type="object", group="Options"),
        OperationParamSpec(name="author", type="object", group="Options"),
    ),
)

GITHUB_WORKFLOW_DISPATCH_SPEC = OperationSpec(
    node_id="github_workflow_dispatch_v2",
    name="GitHub Dispatch Workflow",
    provider="github",
    resource="workflow",
    operation="dispatch",
    description="Trigger a GitHub Actions workflow_dispatch event.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(
            name="workflow_id",
            required=True,
            placeholder="ci.yml or workflow ID",
        ),
        OperationParamSpec(name="ref", required=True, placeholder="main"),
        OperationParamSpec(name="inputs", type="object", group="Options"),
    ),
)

GITHUB_CREATE_RELEASE_SPEC = OperationSpec(
    node_id="github_create_release_v2",
    name="GitHub Create Release",
    provider="github",
    resource="release",
    operation="create",
    description="Create a GitHub release for a tag.",
    icon="brand:github",
    params=(
        _credentials_param(),
        OperationParamSpec(name="repo", required=True, placeholder="owner/name"),
        OperationParamSpec(name="tag_name", required=True, placeholder="v1.0.0"),
        OperationParamSpec(name="name", group="Options", placeholder="Release title"),
        OperationParamSpec(name="body", multiline=True, group="Options"),
        OperationParamSpec(name="target_commitish", group="Options", placeholder="main"),
        OperationParamSpec(name="draft", type="boolean", default=False, group="Options"),
        OperationParamSpec(name="prerelease", type="boolean", default=False, group="Options"),
        OperationParamSpec(
            name="generate_release_notes",
            type="boolean",
            default=False,
            group="Options",
        ),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str):
        return {"token": value}
    return {}


def _token(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("token") or creds.get("access_token") or "")


def _transport(credentials: Any) -> ProviderTransport:
    token = _token(credentials)
    if not token:
        raise ValueError("github v2: credentials are required")
    return ProviderTransport(
        provider="github",
        base_url=GITHUB_API_BASE,
        default_headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _repo_path(repo: str) -> str:
    clean = str(repo or "").strip().strip("/")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", clean):
        raise ValueError("github v2: repo must be in owner/name format")
    return clean


def _labels(value: Any) -> list[str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        labels = [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
        return labels or None
    if isinstance(value, (list, tuple, set)):
        labels = [str(item).strip() for item in value if str(item).strip()]
        return labels or None
    return [str(value).strip()]


def _csv(value: Any) -> str:
    labels = _labels(value)
    return ",".join(labels or [])


def _string_list(value: Any) -> list[str] | None:
    return _labels(value)


def _input_dict(input_value: Any) -> dict[str, Any]:
    return input_value if isinstance(input_value, dict) else {}


def _per_page(value: Any, default: int = 30) -> int:
    return max(1, min(100, int(value or default)))


def _issue_number(node_id: str, value: Any) -> int:
    number = int(value or 0)
    if number <= 0:
        raise ValueError(f"{node_id}: issue_number is required")
    return number


def _pull_number(node_id: str, value: Any) -> int:
    number = int(value or 0)
    if number <= 0:
        raise ValueError(f"{node_id}: pull_number is required")
    return number


def _require(value: str, node_id: str, name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: {name} is required")
    return clean


def _encoded_file_content(content: str, content_base64: str) -> str:
    if content_base64:
        return content_base64
    return base64.b64encode(str(content or "").encode("utf-8")).decode("ascii")


def _payload_without_blanks(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != "" and value != []
    }


def _text_from_input(input_value: Any) -> str:
    if input_value is None:
        return ""
    if isinstance(input_value, str):
        return input_value
    return json.dumps(input_value, default=str)


def get_repository(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    repo: str = "",
) -> Any:
    return _transport(credentials).request(
        "GET",
        f"/repos/{_repo_path(repo)}",
        operation="get_repository",
    )


def create_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    repo: str = "",
    title: str = "",
    body: str = "",
    labels: list[str] | str | None = None,
) -> Any:
    source = input if isinstance(input, dict) else {}
    issue_title = title or str(source.get("title") or "")
    if not issue_title:
        raise ValueError("github_create_issue_v2: title is required")
    issue_body = body or str(source.get("body") or "") or _text_from_input(input)
    payload: dict[str, Any] = {"title": issue_title, "body": issue_body}
    issue_labels = _labels(labels if labels not in (None, "") else source.get("labels"))
    if issue_labels:
        payload["labels"] = issue_labels
    return _transport(credentials).request(
        "POST",
        f"/repos/{_repo_path(repo)}/issues",
        operation="create_issue",
        json_body=payload,
    )


def list_issues(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    repo: str = "",
    state: str = "open",
    labels: list[str] | str | None = None,
    assignee: str = "",
    since: str = "",
    per_page: int = 30,
) -> Any:
    params = _payload_without_blanks(
        {
            "state": state or "open",
            "labels": _csv(labels),
            "assignee": assignee,
            "since": since,
            "per_page": _per_page(per_page),
        }
    )
    return _transport(credentials).request(
        "GET",
        f"/repos/{_repo_path(repo)}/issues",
        operation="list_issues",
        params=params,
    )


def get_issue(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    repo: str = "",
    issue_number: int = 0,
) -> Any:
    number = _issue_number("github_get_issue_v2", issue_number)
    return _transport(credentials).request(
        "GET",
        f"/repos/{_repo_path(repo)}/issues/{number}",
        operation="get_issue",
    )


def update_issue(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    repo: str = "",
    issue_number: int = 0,
    title: str = "",
    body: str = "",
    state: str = "",
    state_reason: str = "",
    labels: list[str] | str | None = None,
    assignees: list[str] | str | None = None,
    milestone: int | None = None,
) -> Any:
    source = _input_dict(input)
    number = _issue_number("github_update_issue_v2", issue_number)
    payload = _payload_without_blanks(
        {
            "title": title or source.get("title"),
            "body": body or source.get("body"),
            "state": state or source.get("state"),
            "state_reason": state_reason or source.get("state_reason"),
            "labels": _string_list(labels if labels not in (None, "") else source.get("labels")),
            "assignees": _string_list(
                assignees if assignees not in (None, "") else source.get("assignees")
            ),
            "milestone": milestone if milestone is not None else source.get("milestone"),
        }
    )
    if not payload:
        raise ValueError("github_update_issue_v2: at least one field is required")
    return _transport(credentials).request(
        "PATCH",
        f"/repos/{_repo_path(repo)}/issues/{number}",
        operation="update_issue",
        json_body=payload,
    )


def create_issue_comment(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    repo: str = "",
    issue_number: int = 0,
    body: str = "",
) -> Any:
    number = _issue_number("github_create_issue_comment_v2", issue_number)
    comment_body = body or _text_from_input(input)
    if not comment_body:
        raise ValueError("github_create_issue_comment_v2: body is required")
    return _transport(credentials).request(
        "POST",
        f"/repos/{_repo_path(repo)}/issues/{number}/comments",
        operation="create_issue_comment",
        json_body={"body": comment_body},
    )


def list_issue_comments(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    repo: str = "",
    issue_number: int = 0,
    since: str = "",
    per_page: int = 30,
) -> Any:
    number = _issue_number("github_list_issue_comments_v2", issue_number)
    params = _payload_without_blanks(
        {"since": since, "per_page": _per_page(per_page)}
    )
    return _transport(credentials).request(
        "GET",
        f"/repos/{_repo_path(repo)}/issues/{number}/comments",
        operation="list_issue_comments",
        params=params,
    )


def list_pull_requests(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    repo: str = "",
    state: str = "open",
    head: str = "",
    base: str = "",
    sort: str = "created",
    direction: str = "desc",
    per_page: int = 30,
) -> Any:
    params = _payload_without_blanks(
        {
            "state": state or "open",
            "head": head,
            "base": base,
            "sort": sort or "created",
            "direction": direction or "desc",
            "per_page": _per_page(per_page),
        }
    )
    return _transport(credentials).request(
        "GET",
        f"/repos/{_repo_path(repo)}/pulls",
        operation="list_pull_requests",
        params=params,
    )


def get_pull_request(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    repo: str = "",
    pull_number: int = 0,
) -> Any:
    number = _pull_number("github_get_pull_request_v2", pull_number)
    return _transport(credentials).request(
        "GET",
        f"/repos/{_repo_path(repo)}/pulls/{number}",
        operation="get_pull_request",
    )


def create_pull_request(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    repo: str = "",
    title: str = "",
    head: str = "",
    base: str = "",
    body: str = "",
    draft: bool = False,
    maintainer_can_modify: bool = True,
) -> Any:
    source = _input_dict(input)
    payload = _payload_without_blanks(
        {
            "title": title or source.get("title"),
            "head": head or source.get("head"),
            "base": base or source.get("base"),
            "body": body or source.get("body"),
            "draft": bool(draft),
            "maintainer_can_modify": bool(maintainer_can_modify),
        }
    )
    for field in ("title", "head", "base"):
        if not payload.get(field):
            raise ValueError(f"github_create_pull_request_v2: {field} is required")
    return _transport(credentials).request(
        "POST",
        f"/repos/{_repo_path(repo)}/pulls",
        operation="create_pull_request",
        json_body=payload,
    )


def merge_pull_request(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    repo: str = "",
    pull_number: int = 0,
    commit_title: str = "",
    commit_message: str = "",
    merge_method: str = "merge",
) -> Any:
    number = _pull_number("github_merge_pull_request_v2", pull_number)
    payload = _payload_without_blanks(
        {
            "commit_title": commit_title,
            "commit_message": commit_message,
            "merge_method": merge_method or "merge",
        }
    )
    return _transport(credentials).request(
        "PUT",
        f"/repos/{_repo_path(repo)}/pulls/{number}/merge",
        operation="merge_pull_request",
        json_body=payload,
    )


def get_file_contents(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    repo: str = "",
    path: str = "",
    ref: str = "",
) -> Any:
    clean_path = str(path or "").strip().lstrip("/")
    if not clean_path:
        raise ValueError("github_get_file_contents_v2: path is required")
    params = {"ref": ref} if ref else None
    return _transport(credentials).request(
        "GET",
        f"/repos/{_repo_path(repo)}/contents/{quote(clean_path, safe='/')}",
        operation="get_file_contents",
        params=params,
    )


def put_file_contents(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    repo: str = "",
    path: str = "",
    message: str = "",
    content: str = "",
    content_base64: str = "",
    branch: str = "",
    sha: str = "",
    committer: dict[str, Any] | None = None,
    author: dict[str, Any] | None = None,
) -> Any:
    clean_path = _require(path, "github_put_file_contents_v2", "path").lstrip("/")
    source = _input_dict(input)
    commit_message = message or str(source.get("message") or "")
    if not commit_message:
        raise ValueError("github_put_file_contents_v2: message is required")
    payload = _payload_without_blanks(
        {
            "message": commit_message,
            "content": _encoded_file_content(
                content or str(source.get("content") or ""),
                content_base64 or str(source.get("content_base64") or ""),
            ),
            "branch": branch or source.get("branch"),
            "sha": sha or source.get("sha"),
            "committer": committer if committer is not None else source.get("committer"),
            "author": author if author is not None else source.get("author"),
        }
    )
    return _transport(credentials).request(
        "PUT",
        f"/repos/{_repo_path(repo)}/contents/{quote(clean_path, safe='/')}",
        operation="put_file_contents",
        json_body=payload,
    )


def workflow_dispatch(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    repo: str = "",
    workflow_id: str = "",
    ref: str = "",
    inputs: dict[str, Any] | None = None,
) -> Any:
    workflow = str(workflow_id or "").strip()
    if not workflow:
        raise ValueError("github_workflow_dispatch_v2: workflow_id is required")
    if not ref:
        raise ValueError("github_workflow_dispatch_v2: ref is required")
    source = _input_dict(input)
    payload = {"ref": ref, "inputs": inputs if inputs is not None else source.get("inputs", {})}
    return _transport(credentials).request(
        "POST",
        f"/repos/{_repo_path(repo)}/actions/workflows/{quote(workflow, safe='')}/dispatches",
        operation="workflow_dispatch",
        json_body=payload,
    )


def create_release(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    repo: str = "",
    tag_name: str = "",
    name: str = "",
    body: str = "",
    target_commitish: str = "",
    draft: bool = False,
    prerelease: bool = False,
    generate_release_notes: bool = False,
) -> Any:
    source = _input_dict(input)
    tag = tag_name or str(source.get("tag_name") or "")
    if not tag:
        raise ValueError("github_create_release_v2: tag_name is required")
    payload = _payload_without_blanks(
        {
            "tag_name": tag,
            "name": name or source.get("name"),
            "body": body or source.get("body"),
            "target_commitish": target_commitish or source.get("target_commitish"),
            "draft": bool(draft),
            "prerelease": bool(prerelease),
            "generate_release_notes": bool(generate_release_notes),
        }
    )
    return _transport(credentials).request(
        "POST",
        f"/repos/{_repo_path(repo)}/releases",
        operation="create_release",
        json_body=payload,
    )


register_operation(GITHUB_GET_REPO_SPEC, get_repository)
register_operation(GITHUB_CREATE_ISSUE_SPEC, create_issue)
register_operation(GITHUB_LIST_ISSUES_SPEC, list_issues)
register_operation(GITHUB_GET_ISSUE_SPEC, get_issue)
register_operation(GITHUB_UPDATE_ISSUE_SPEC, update_issue)
register_operation(GITHUB_CREATE_ISSUE_COMMENT_SPEC, create_issue_comment)
register_operation(GITHUB_LIST_ISSUE_COMMENTS_SPEC, list_issue_comments)
register_operation(GITHUB_LIST_PULL_REQUESTS_SPEC, list_pull_requests)
register_operation(GITHUB_GET_PULL_REQUEST_SPEC, get_pull_request)
register_operation(GITHUB_CREATE_PULL_REQUEST_SPEC, create_pull_request)
register_operation(GITHUB_MERGE_PULL_REQUEST_SPEC, merge_pull_request)
register_operation(GITHUB_GET_FILE_CONTENTS_SPEC, get_file_contents)
register_operation(GITHUB_PUT_FILE_CONTENTS_SPEC, put_file_contents)
register_operation(GITHUB_WORKFLOW_DISPATCH_SPEC, workflow_dispatch)
register_operation(GITHUB_CREATE_RELEASE_SPEC, create_release)
