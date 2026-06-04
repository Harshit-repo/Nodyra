"""GitHub v2 operation specs and executors."""

from __future__ import annotations

import json
import re
from typing import Any

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


register_operation(GITHUB_GET_REPO_SPEC, get_repository)
register_operation(GITHUB_CREATE_ISSUE_SPEC, create_issue)
