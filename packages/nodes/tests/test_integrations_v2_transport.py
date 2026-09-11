import json

import pytest

from nodyra.context import node_debug
from nodyra_nodes.http_security import UnsafeHttpTargetError
from nodyra_nodes.integrations_v2 import ProviderError, ProviderTransport, RetryPolicy
from nodyra_nodes.integrations_v2.providers.google import GoogleTransport
from nodyra_nodes.integrations_v2.providers.microsoft import MicrosoftGraphTransport


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


def test_transport_retries_rate_limit_and_returns_json(monkeypatch) -> None:
    responses = [
        FakeResponse(429, {"error": {"code": "rateLimit", "message": "slow down"}}),
        FakeResponse(200, {"ok": True}),
    ]
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return responses.pop(0)

    monkeypatch.setattr("requests.request", fake_request)
    transport = ProviderTransport(
        provider="demo",
        base_url="https://api.example/v1",
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0),
    )

    debug: dict = {}
    token = node_debug.set(debug)
    try:
        result = transport.request(
            "GET",
            "/things?api_key=secret",
            operation="list_things",
        )
    finally:
        node_debug.reset(token)

    assert result == {"ok": True}
    assert [call["url"] for call in calls] == [
        "https://api.example/v1/things?api_key=secret",
        "https://api.example/v1/things?api_key=secret",
    ]
    events = debug["provider_requests"]
    assert len(events) == 2
    assert events[0] == {
        "provider": "demo",
        "operation": "list_things",
        "method": "GET",
        "url": "https://api.example/v1/things",
        "attempt": 1,
        "max_attempts": 2,
        "latency_ms": events[0]["latency_ms"],
        "outcome": "retry_scheduled",
        "retryable": True,
        "retry_scheduled": True,
        "status_code": 429,
        "error_code": "rateLimit",
    }
    assert events[1] == {
        "provider": "demo",
        "operation": "list_things",
        "method": "GET",
        "url": "https://api.example/v1/things",
        "attempt": 2,
        "max_attempts": 2,
        "latency_ms": events[1]["latency_ms"],
        "outcome": "success",
        "retryable": False,
        "retry_scheduled": False,
        "status_code": 200,
    }
    assert isinstance(events[0]["latency_ms"], int)
    assert isinstance(events[1]["latency_ms"], int)
    assert "secret" not in json.dumps(events).lower()


def test_transport_error_is_structured_and_does_not_include_headers(monkeypatch) -> None:
    def fake_request(method: str, url: str, **kwargs):  # noqa: ARG001
        return FakeResponse(
            401,
            {
                "error": {
                    "code": "InvalidAuthenticationToken",
                    "message": "Access token is invalid",
                }
            },
            headers={"x-ms-request-id": "request-123"},
        )

    monkeypatch.setattr("requests.request", fake_request)
    transport = ProviderTransport(
        provider="microsoft_graph",
        base_url="https://graph.microsoft.com/v1.0",
        default_headers={"Authorization": "Bearer secret-token"},
        retry_policy=RetryPolicy(max_attempts=1),
    )

    with pytest.raises(ProviderError) as raised:
        transport.request("GET", "/me/messages", operation="list_messages")

    error = raised.value
    assert error.provider == "microsoft_graph"
    assert error.operation == "list_messages"
    assert error.status_code == 401
    assert error.code == "InvalidAuthenticationToken"
    assert error.message == "Access token is invalid"
    assert error.retryable is False
    assert error.request_id == "request-123"
    assert "secret-token" not in str(error.to_dict())


def test_exhausted_retryable_error_remains_marked_retryable(monkeypatch) -> None:
    def fake_request(method: str, url: str, **kwargs):  # noqa: ARG001
        return FakeResponse(503, {"error": "temporarily_unavailable"})

    monkeypatch.setattr("requests.request", fake_request)
    transport = ProviderTransport(
        provider="demo",
        base_url="https://api.example",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    with pytest.raises(ProviderError) as raised:
        transport.request("GET", "/status", operation="status")

    assert raised.value.status_code == 503
    assert raised.value.retryable is True


def test_google_transport_adds_bearer_and_api_key(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse(200, {"values": [["A1"]]})

    monkeypatch.setattr("requests.request", fake_request)
    transport = GoogleTransport(
        access_token="google-token",
        api_key="google-api-key",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = transport.request(
        "GET",
        "/spreadsheets/sheet-id/values/Sheet1!A1",
        operation="read_values",
    )

    assert result == {"values": [["A1"]]}
    assert calls[0]["url"].startswith("https://sheets.googleapis.com/v4/")
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer google-token"
    assert calls[0]["kwargs"]["params"]["key"] == "google-api-key"


def test_transport_blocks_private_host_ssrf(monkeypatch) -> None:
    """SEC-1: a user-controlled base_url pointing at a private/metadata host
    must be rejected before any HTTP request is issued."""
    issued: list[str] = []

    def fake_request(method: str, url: str, **kwargs):  # noqa: ARG001
        issued.append(url)
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr("requests.request", fake_request)
    transport = ProviderTransport(
        provider="gitlab",
        base_url="http://169.254.169.254",
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0),
    )

    with pytest.raises(UnsafeHttpTargetError):
        transport.request("GET", "/api/v4/user", operation="get_user")

    # The request must never have been dispatched, and SSRF must not be retried.
    assert issued == []


def test_transport_blocks_absolute_url_override_to_private_host(monkeypatch) -> None:
    """SEC-1: passing an absolute http(s) URL that targets a private host is
    rejected even though url() passes absolute URLs through untouched."""

    def fake_request(method: str, url: str, **kwargs):  # noqa: ARG001
        raise AssertionError("request must not be dispatched for a blocked host")

    monkeypatch.setattr("requests.request", fake_request)
    transport = ProviderTransport(
        provider="demo",
        base_url="https://api.example",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    with pytest.raises(UnsafeHttpTargetError):
        transport.request("GET", "http://127.0.0.1:8080/admin", operation="probe")


def test_transport_allows_public_host(monkeypatch) -> None:
    """SEC-1: ordinary public hosts continue to work unchanged."""
    calls: list[str] = []

    def fake_request(method: str, url: str, **kwargs):  # noqa: ARG001
        calls.append(url)
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr("requests.request", fake_request)
    transport = ProviderTransport(
        provider="demo",
        base_url="https://api.example/v1",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = transport.request("GET", "/things", operation="list_things")
    assert result == {"ok": True}
    assert calls == ["https://api.example/v1/things"]


def test_microsoft_graph_transport_sets_base_url_and_bearer(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse(200, {"value": []})

    monkeypatch.setattr("requests.request", fake_request)
    transport = MicrosoftGraphTransport(
        access_token="graph-token",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = transport.request("GET", "/me/messages", operation="list_messages")

    assert result == {"value": []}
    assert calls[0]["url"] == "https://graph.microsoft.com/v1.0/me/messages"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer graph-token"

@pytest.fixture(autouse=True)
def _blocked_egress_posture(monkeypatch):
    """These tests verify the BLOCKED posture of nodyra_nodes.http_security;
    single-tenant API processes default to allowing private egress (mirroring
    workers), so pin the env explicitly."""
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "0")

