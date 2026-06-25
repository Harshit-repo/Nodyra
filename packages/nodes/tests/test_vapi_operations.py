"""Tests for Vapi.ai v2 operations and poll trigger."""
from unittest.mock import MagicMock, patch

from noodle_nodes.integrations_v2.providers.vapi.operations import (
    create_assistant,
    end_call,
    get_call,
    list_calls,
    poll_completed_calls,
    start_call,
)
from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext

_TRANSPORT_PATH = "noodle_nodes.integrations_v2.providers.vapi.operations._transport"
_CREDS = {"api_key": "vapi_test_key"}


def _mock_transport(return_value: object) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_start_call() -> None:
    mock_transport = _mock_transport({"id": "call1"})
    with patch(_TRANSPORT_PATH, return_value=mock_transport):
        result = start_call(
            credentials=_CREDS,
            assistant_id="asst1",
            phone_number_id="ph1",
            customer_number="+1555000",
        )
    assert result == {"id": "call1"}
    call_args = mock_transport.request.call_args
    assert call_args[0][0] == "POST"
    assert call_args[0][1] == "/call"
    body = call_args[1]["json_body"]
    assert body["assistantId"] == "asst1"
    assert body["customer"]["number"] == "+1555000"


def test_get_call() -> None:
    mock_transport = _mock_transport({"id": "call1", "status": "ended"})
    with patch(_TRANSPORT_PATH, return_value=mock_transport):
        result = get_call(credentials=_CREDS, call_id="call1")
    assert result["status"] == "ended"
    call_args = mock_transport.request.call_args
    assert call_args[0][0] == "GET"
    assert call_args[0][1] == "/call/call1"


def test_list_calls() -> None:
    mock_transport = _mock_transport([{"id": "c1"}])
    with patch(_TRANSPORT_PATH, return_value=mock_transport):
        result = list_calls(credentials=_CREDS, limit=10)
    assert result == [{"id": "c1"}]
    call_args = mock_transport.request.call_args
    assert call_args[0][0] == "GET"
    assert call_args[0][1] == "/call"
    assert call_args[1]["params"]["limit"] == 10


def test_end_call() -> None:
    mock_transport = _mock_transport({"id": "call1", "status": "ended"})
    with patch(_TRANSPORT_PATH, return_value=mock_transport):
        end_call(credentials=_CREDS, call_id="call1")
    call_args = mock_transport.request.call_args
    assert call_args[0][0] == "DELETE"
    assert call_args[0][1] == "/call/call1"


def test_create_assistant() -> None:
    mock_transport = _mock_transport({"id": "asst1"})
    with patch(_TRANSPORT_PATH, return_value=mock_transport):
        result = create_assistant(
            credentials=_CREDS,
            name="Bob",
            first_message="Hello",
            system_prompt="Be helpful.",
        )
    assert result == {"id": "asst1"}
    call_args = mock_transport.request.call_args
    assert call_args[0][0] == "POST"
    assert call_args[0][1] == "/assistant"
    body = call_args[1]["json_body"]
    assert body["name"] == "Bob"
    assert body["firstMessage"] == "Hello"


def test_create_assistant_with_config() -> None:
    raw_config = '{"name": "raw", "model": {}}'
    mock_transport = _mock_transport({"id": "asst2"})
    with patch(_TRANSPORT_PATH, return_value=mock_transport):
        create_assistant(
            credentials=_CREDS,
            name="ignored",
            assistant_config=raw_config,
        )
    call_args = mock_transport.request.call_args
    body = call_args[1]["json_body"]
    # Should use raw config, not name-based body
    assert body["name"] == "raw"
    assert body["model"] == {}
    # Should NOT have firstMessage etc from individual params
    assert "firstMessage" not in body


def test_poll_first_run_returns_no_events() -> None:
    mock_transport = _mock_transport([
        {"id": "c1", "createdAt": "2026-01-01T00:00:00Z", "status": "ended"}
    ])
    with patch(_TRANSPORT_PATH, return_value=mock_transport):
        ctx = ProviderTriggerPollContext(params={"credentials": _CREDS}, cursor={})
        result = poll_completed_calls(ctx)
    assert result.events == []
    assert result.cursor["last_created_at"] == "2026-01-01T00:00:00Z"


def test_poll_subsequent_run_emits_new_calls() -> None:
    mock_transport = _mock_transport([
        {"id": "c1", "createdAt": "2026-02-01T00:00:00Z", "status": "ended"}
    ])
    with patch(_TRANSPORT_PATH, return_value=mock_transport):
        ctx = ProviderTriggerPollContext(
            params={"credentials": _CREDS},
            cursor={"last_created_at": "2025-01-01T00:00:00Z", "seen_ids": []},
        )
        result = poll_completed_calls(ctx)
    assert len(result.events) == 1
    assert result.events[0]["id"] == "c1"
