from typing import Any
from unittest.mock import MagicMock

import pytest

import nodyra_nodes  # noqa: F401 - importing registers provider nodes
from nodyra.sdk import registry
from nodyra_nodes.integrations_v2.providers.github import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_github_v2_nodes_are_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    expected = {
        "github_get_repo_v2": ("GitHub Get Repository", False),
        "github_create_issue_v2": ("GitHub Create Issue", True),
        "github_list_issues_v2": ("GitHub List Issues", False),
        "github_get_issue_v2": ("GitHub Get Issue", False),
        "github_update_issue_v2": ("GitHub Update Issue", True),
        "github_create_issue_comment_v2": ("GitHub Create Issue Comment", True),
        "github_list_issue_comments_v2": ("GitHub List Issue Comments", False),
        "github_list_pull_requests_v2": ("GitHub List Pull Requests", False),
        "github_get_pull_request_v2": ("GitHub Get Pull Request", False),
        "github_create_pull_request_v2": ("GitHub Create Pull Request", True),
        "github_merge_pull_request_v2": ("GitHub Merge Pull Request", True),
        "github_get_file_contents_v2": ("GitHub Get File Contents", False),
        "github_put_file_contents_v2": ("GitHub Create Or Update File", True),
        "github_workflow_dispatch_v2": ("GitHub Dispatch Workflow", True),
        "github_create_release_v2": ("GitHub Create Release", True),
    }

    for node_id, (name, side_effecting) in expected.items():
        manifest = manifests[node_id]
        assert manifest.name == name
        assert manifest.icon == "brand:github"
        assert manifest.category == "Integrations"
        assert manifest.usable_as_tool is True
        assert manifest.tool_side_effecting is side_effecting

    params = {param.name: param for param in manifests["github_create_issue_v2"].params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "github"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "github"
    assert params["body"].group == "Options"


def test_github_v2_generated_source_is_available() -> None:
    source = getattr(registry.get("github_create_issue_v2").func, "__nodyra_source__", "")

    assert "def github_create_issue_v2(" in source
    assert "credentials=None" in source
    assert "execute_registered_operation" in source
    assert "github.issue.create" in source


def test_github_get_repo_v2_uses_transport(monkeypatch) -> None:
    transport = _mock_transport({"full_name": "octo/hello"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("github_get_repo_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
    )

    assert result == {"full_name": "octo/hello"}
    transport.request.assert_called_once_with(
        "GET",
        "/repos/octo/hello",
        operation="get_repository",
    )


def test_github_create_issue_v2_uses_input_and_labels(monkeypatch) -> None:
    transport = _mock_transport({"number": 123})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("github_create_issue_v2").func(
        input={"title": "Fallback title", "body": "Fallback body"},
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        title="Issue title",
        labels="bug, urgent",
    )

    assert result == {"number": 123}
    transport.request.assert_called_once_with(
        "POST",
        "/repos/octo/hello/issues",
        operation="create_issue",
        json_body={
            "title": "Issue title",
            "body": "Fallback body",
            "labels": ["bug", "urgent"],
        },
    )


def test_github_create_issue_v2_requires_valid_repo_and_title() -> None:
    with pytest.raises(ValueError, match="owner/name"):
        operations.get_repository(credentials={"token": "ghp-test"}, repo="bad")

    with pytest.raises(ValueError, match="title"):
        operations.create_issue(credentials={"token": "ghp-test"}, repo="octo/hello")


def test_github_list_issues_v2_builds_filters(monkeypatch) -> None:
    transport = _mock_transport([])
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("github_list_issues_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        state="all",
        labels=["bug", "urgent"],
        assignee="octocat",
        since="2024-01-01T00:00:00Z",
        per_page=250,
    )

    transport.request.assert_called_once_with(
        "GET",
        "/repos/octo/hello/issues",
        operation="list_issues",
        params={
            "state": "all",
            "labels": "bug,urgent",
            "assignee": "octocat",
            "since": "2024-01-01T00:00:00Z",
            "per_page": 100,
        },
    )


def test_github_update_issue_v2_payload(monkeypatch) -> None:
    transport = _mock_transport({"number": 7})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("github_update_issue_v2").func(
        input={"body": "Updated body"},
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        issue_number=7,
        state="closed",
        labels="done, shipped",
        assignees=["octocat"],
    )

    transport.request.assert_called_once_with(
        "PATCH",
        "/repos/octo/hello/issues/7",
        operation="update_issue",
        json_body={
            "body": "Updated body",
            "state": "closed",
            "labels": ["done", "shipped"],
            "assignees": ["octocat"],
        },
    )


def test_github_issue_comment_nodes(monkeypatch) -> None:
    transport = _mock_transport({"id": 1})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("github_create_issue_comment_v2").func(
        input={"message": "from input"},
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        issue_number=3,
    )
    registry.get("github_list_issue_comments_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        issue_number=3,
        per_page=5,
    )

    assert transport.request.call_args_list[0].args == (
        "POST",
        "/repos/octo/hello/issues/3/comments",
    )
    assert transport.request.call_args_list[0].kwargs == {
        "operation": "create_issue_comment",
        "json_body": {"body": '{"message": "from input"}'},
    }
    assert transport.request.call_args_list[1].args == (
        "GET",
        "/repos/octo/hello/issues/3/comments",
    )
    assert transport.request.call_args_list[1].kwargs == {
        "operation": "list_issue_comments",
        "params": {"per_page": 5},
    }


def test_github_pr_contents_workflow_and_release_nodes(monkeypatch) -> None:
    transport = _mock_transport({})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("github_list_pull_requests_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        state="all",
        base="main",
        per_page=2,
    )
    registry.get("github_get_file_contents_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        path="docs/read me.md",
        ref="main",
    )
    registry.get("github_workflow_dispatch_v2").func(
        input={"inputs": {"env": "prod"}},
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        workflow_id="ci.yml",
        ref="main",
    )
    registry.get("github_create_release_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        tag_name="v1.0.0",
        name="v1.0.0",
        draft=True,
    )

    assert transport.request.call_args_list[0].args == ("GET", "/repos/octo/hello/pulls")
    assert transport.request.call_args_list[0].kwargs["params"]["base"] == "main"
    assert transport.request.call_args_list[1].args == (
        "GET",
        "/repos/octo/hello/contents/docs/read%20me.md",
    )
    assert transport.request.call_args_list[1].kwargs == {
        "operation": "get_file_contents",
        "params": {"ref": "main"},
    }
    assert transport.request.call_args_list[2].args == (
        "POST",
        "/repos/octo/hello/actions/workflows/ci.yml/dispatches",
    )
    assert transport.request.call_args_list[2].kwargs == {
        "operation": "workflow_dispatch",
        "json_body": {"ref": "main", "inputs": {"env": "prod"}},
    }
    assert transport.request.call_args_list[3].args == (
        "POST",
        "/repos/octo/hello/releases",
    )
    assert transport.request.call_args_list[3].kwargs == {
        "operation": "create_release",
        "json_body": {
            "tag_name": "v1.0.0",
            "name": "v1.0.0",
            "draft": True,
            "prerelease": False,
            "generate_release_notes": False,
        },
    }


def test_github_pull_request_mutation_nodes(monkeypatch) -> None:
    transport = _mock_transport({})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("github_get_pull_request_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        pull_number=9,
    )
    registry.get("github_create_pull_request_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        title="Add report",
        head="feature/report",
        base="main",
        body="Ready",
    )
    registry.get("github_merge_pull_request_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        pull_number=9,
        merge_method="squash",
    )

    assert transport.request.call_args_list[0].args == ("GET", "/repos/octo/hello/pulls/9")
    assert transport.request.call_args_list[1].args == ("POST", "/repos/octo/hello/pulls")
    assert transport.request.call_args_list[1].kwargs == {
        "operation": "create_pull_request",
        "json_body": {
            "title": "Add report",
            "head": "feature/report",
            "base": "main",
            "body": "Ready",
            "draft": False,
            "maintainer_can_modify": True,
        },
    }
    assert transport.request.call_args_list[2].args == (
        "PUT",
        "/repos/octo/hello/pulls/9/merge",
    )
    assert transport.request.call_args_list[2].kwargs == {
        "operation": "merge_pull_request",
        "json_body": {"merge_method": "squash"},
    }


def test_github_put_file_contents_v2_encodes_content(monkeypatch) -> None:
    transport = _mock_transport({"content": {"sha": "next"}})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("github_put_file_contents_v2").func(
        input=None,
        credentials={"token": "ghp-test"},
        repo="octo/hello",
        path="docs/report.md",
        message="Update report",
        content="hello",
        branch="main",
        sha="abc123",
    )

    transport.request.assert_called_once_with(
        "PUT",
        "/repos/octo/hello/contents/docs/report.md",
        operation="put_file_contents",
        json_body={
            "message": "Update report",
            "content": "aGVsbG8=",
            "branch": "main",
            "sha": "abc123",
        },
    )
