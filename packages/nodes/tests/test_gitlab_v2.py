"""Tests for GitLab v2 integration operations."""

from typing import Any
from unittest.mock import MagicMock

import pytest

import noodle_nodes  # noqa: F401 - registers provider nodes
from noodle_nodes.integrations_v2.providers.gitlab import operations
from noodle_nodes.integrations_v2.registry import get_registered_operation


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_gitlab_operations_are_registered() -> None:
    expected_ids = [
        "gitlab_list_projects_v2",
        "gitlab_get_project_v2",
        "gitlab_list_issues_v2",
        "gitlab_create_issue_v2",
        "gitlab_list_merge_requests_v2",
        "gitlab_create_merge_request_v2",
        "gitlab_get_file_v2",
        "gitlab_trigger_pipeline_v2",
    ]
    for node_id in expected_ids:
        op = get_registered_operation(node_id)
        assert op.spec.node_id == node_id, f"expected {node_id}, got {op.spec.node_id}"


def test_gitlab_trigger_pipeline_spec() -> None:
    op = get_registered_operation("gitlab_trigger_pipeline_v2")
    assert op.spec.name == "GitLab Trigger Pipeline"
    assert op.spec.tool_side_effecting is True
    assert op.spec.resource == "pipeline"
    assert op.spec.operation == "trigger"
    param_names = {p.name for p in op.spec.params}
    assert "credentials" in param_names
    assert "project_id" in param_names
    assert "ref" in param_names
    assert "variables" in param_names


def test_gitlab_trigger_pipeline_uses_transport(monkeypatch) -> None:
    transport = _mock_transport({"id": 42, "status": "created", "ref": "main"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = operations.trigger_pipeline(
        input=None,
        credentials={"server_url": "https://gitlab.com", "access_token": "glpat-test"},
        project_id="mygroup/myproject",
        ref="main",
    )

    assert result["id"] == 42
    assert result["status"] == "created"
    transport.request.assert_called_once()
    call_args = transport.request.call_args
    assert call_args[0][0] == "POST"
    assert "pipeline" in call_args[0][1]
    assert call_args[1]["json_body"]["ref"] == "main"


def test_gitlab_trigger_pipeline_with_variables(monkeypatch) -> None:
    transport = _mock_transport({"id": 99, "status": "created"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    operations.trigger_pipeline(
        input=None,
        credentials={"access_token": "glpat-test"},
        project_id="123",
        ref="develop",
        variables={"ENV": "staging", "VERSION": "1.2.3"},
    )

    call_args = transport.request.call_args
    payload = call_args[1]["json_body"]
    assert payload["ref"] == "develop"
    vars_by_key = {v["key"]: v["value"] for v in payload["variables"]}
    assert vars_by_key["ENV"] == "staging"
    assert vars_by_key["VERSION"] == "1.2.3"


def test_gitlab_trigger_pipeline_requires_project_id() -> None:
    with pytest.raises(ValueError, match="project_id"):
        operations.trigger_pipeline(credentials={"access_token": "x"}, project_id="", ref="main")


def test_gitlab_trigger_pipeline_requires_ref() -> None:
    with pytest.raises(ValueError, match="ref"):
        operations.trigger_pipeline(credentials={"access_token": "x"}, project_id="123", ref="")
