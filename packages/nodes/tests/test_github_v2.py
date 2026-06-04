from typing import Any
from unittest.mock import MagicMock

import pytest

import noodle_nodes  # noqa: F401 - importing registers provider nodes
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.github import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_github_v2_nodes_are_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}

    get_repo = manifests["github_get_repo_v2"]
    create_issue = manifests["github_create_issue_v2"]

    assert get_repo.name == "GitHub Get Repository"
    assert create_issue.name == "GitHub Create Issue"
    assert get_repo.icon == "brand:github"
    assert create_issue.icon == "brand:github"
    assert get_repo.category == "Integrations"
    assert create_issue.category == "Integrations"
    params = {param.name: param for param in create_issue.params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "github"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "github"
    assert params["body"].group == "Options"


def test_github_v2_generated_source_is_available() -> None:
    source = getattr(registry.get("github_create_issue_v2").func, "__noodle_source__", "")

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
