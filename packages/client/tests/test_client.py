"""Tests for the nodyra-client typed API client."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from nodyra_client.client import NodyraClient, NodyraError
from nodyra_client.models import WorkflowDetail, WorkflowSummary


def test_client_constructs_with_token():
    client = NodyraClient(base_url="https://nodyra.example.com", token="test-token")
    assert client._base == "https://nodyra.example.com"
    assert client._token == "test-token"


def test_workflows_list_parses_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://nodyra.example.com/workflows?limit=50&offset=0",
        json=[
            {
                "id": "wf-1",
                "name": "Test Workflow",
                "active": True,
                "status": "published",
                "latest_version": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            }
        ],
    )
    client = NodyraClient(base_url="https://nodyra.example.com", token="t")
    result = client.workflows.list()
    assert len(result) == 1
    assert isinstance(result[0], WorkflowSummary)
    assert result[0].name == "Test Workflow"
    assert result[0].status == "published"


def test_workflows_get_parses_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://nodyra.example.com/workflows/wf-1",
        json={
            "id": "wf-1",
            "name": "Test Workflow",
            "status": "draft",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        },
    )
    client = NodyraClient(base_url="https://nodyra.example.com", token="t")
    result = client.workflows.get("wf-1")
    assert isinstance(result, WorkflowDetail)
    assert result.name == "Test Workflow"


def test_workflows_create_sends_body(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url="https://nodyra.example.com/workflows",
        json={
            "id": "wf-new",
            "name": "New Workflow",
            "status": "draft",
        },
    )
    client = NodyraClient(base_url="https://nodyra.example.com", token="t")
    result = client.workflows.create(name="New Workflow")
    assert result.id == "wf-new"


def test_runs_start_parses_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url="https://nodyra.example.com/workflows/wf-1/run",
        json={"id": "run-1", "workflow_id": "wf-1", "status": "queued", "mode": "manual"},
    )
    client = NodyraClient(base_url="https://nodyra.example.com", token="t")
    result = client.runs.start("wf-1", data={"key": "value"})
    assert result.id == "run-1"
    assert result.status == "queued"


def test_error_response_raises_nodyra_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://nodyra.example.com/workflows/wf-404",
        status_code=404,
        json={"detail": "Workflow not found"},
    )
    client = NodyraClient(base_url="https://nodyra.example.com", token="t")
    with pytest.raises(NodyraError) as exc:
        client.workflows.get("wf-404")
    assert exc.value.status == 404
    assert "Workflow not found" in str(exc.value)


def test_auth_header_sent(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://nodyra.example.com/auth/me",
        json={"email": "user@example.com"},
    )
    client = NodyraClient(base_url="https://nodyra.example.com", token="secret")
    result = client.whoami()
    assert result["email"] == "user@example.com"
    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer secret"


def test_client_uses_env_vars(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setenv("NODYRA_BASE_URL", "https://env.example.com")
    monkeypatch.setenv("NODYRA_TOKEN", "env-token")
    httpx_mock.add_response(
        url="https://env.example.com/auth/me",
        json={"email": "env@example.com"},
    )
    client = NodyraClient()  # no explicit args
    assert client.whoami()["email"] == "env@example.com"


def test_client_context_manager_closes(httpx_mock: HTTPXMock):
    """Verify the context manager properly closes the underlying httpx client."""
    httpx_mock.add_response(
        url="https://nodyra.example.com/auth/me",
        json={"email": "test@example.com"},
    )
    with NodyraClient(base_url="https://nodyra.example.com", token="t") as client:
        assert client.whoami()["email"] == "test@example.com"
        assert not client._client.is_closed
    assert client._client.is_closed


def test_base_url_strips_trailing_slash_and_whitespace(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://nodyra.example.com/auth/me",
        json={"email": "test@example.com"},
    )
    client = NodyraClient(base_url="  https://nodyra.example.com/  ", token="t")
    assert client._base == "https://nodyra.example.com"
    assert client.whoami()["email"] == "test@example.com"


def test_negative_timeout_clamped():
    client = NodyraClient(base_url="https://nodyra.example.com", token="t", timeout=-5)
    assert client._timeout == 1.0  # clamped to minimum
    client.close()
