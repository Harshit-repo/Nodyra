"""Tests for Retell AI v2 operations."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from noodle_nodes.integrations_v2.providers.retell.operations import (
    create_agent,
    create_call,
    create_phone_number,
    get_call,
    list_agents,
    list_calls,
)

_CREDS = {"api_key": "retell_test_key"}
_TRANSPORT_PATH = "noodle_nodes.integrations_v2.providers.retell.operations._transport"


def _mock_transport(return_value: object = None) -> tuple[MagicMock, MagicMock]:
    patcher = patch(_TRANSPORT_PATH)
    mock_fn = patcher.start()
    transport = MagicMock()
    mock_fn.return_value = transport
    transport.request.return_value = return_value or {}
    return patcher, transport


# ---------------------------------------------------------------------------
# create_call
# ---------------------------------------------------------------------------


def test_create_call():
    patcher, transport = _mock_transport({"call_id": "call1", "status": "registered"})
    try:
        result = create_call(
            credentials=_CREDS,
            agent_id="agent1",
            from_number="+15005550006",
            to_number="+15005551234",
        )
    finally:
        patcher.stop()
    assert result["call_id"] == "call1"
    _, kw = transport.request.call_args
    assert kw["json_body"]["agent_id"] == "agent1"
    assert kw["json_body"]["to_number"] == "+15005551234"


def test_create_call_with_metadata():
    patcher, transport = _mock_transport({"call_id": "call2"})
    meta = {"campaign": "promo"}
    try:
        create_call(
            credentials=_CREDS,
            agent_id="agent1",
            from_number="+15005550006",
            to_number="+15005551234",
            metadata=json.dumps(meta),
        )
    finally:
        patcher.stop()
    _, kw = transport.request.call_args
    assert kw["json_body"]["metadata"] == meta


def test_create_call_raises_without_required_fields():
    with pytest.raises(ValueError, match="agent_id"):
        create_call(credentials=_CREDS, agent_id="", from_number="+1", to_number="+1")


# ---------------------------------------------------------------------------
# get_call
# ---------------------------------------------------------------------------


def test_get_call():
    patcher, transport = _mock_transport({"call_id": "call1", "status": "ended"})
    try:
        result = get_call(credentials=_CREDS, call_id="call1")
    finally:
        patcher.stop()
    assert result["status"] == "ended"
    args, _ = transport.request.call_args
    assert "call1" in args[1]


# ---------------------------------------------------------------------------
# list_calls
# ---------------------------------------------------------------------------


def test_list_calls():
    patcher, transport = _mock_transport([{"call_id": "c1"}])
    try:
        result = list_calls(credentials=_CREDS, limit=5)
    finally:
        patcher.stop()
    assert isinstance(result, list)
    _, kw = transport.request.call_args
    assert kw["params"]["limit"] == 5


# ---------------------------------------------------------------------------
# create_agent
# ---------------------------------------------------------------------------


def test_create_agent():
    patcher, transport = _mock_transport({"agent_id": "agent1"})
    try:
        result = create_agent(
            credentials=_CREDS,
            agent_name="MyAgent",
            llm_websocket_url="wss://example.com/llm",
            voice_id="voice_abc",
        )
    finally:
        patcher.stop()
    assert result["agent_id"] == "agent1"
    _, kw = transport.request.call_args
    assert kw["json_body"]["agent_name"] == "MyAgent"
    assert kw["json_body"]["voice_id"] == "voice_abc"


def test_create_agent_with_raw_config():
    patcher, transport = _mock_transport({"agent_id": "agent2"})
    raw = {"agent_name": "raw", "voice_id": "v1", "llm_websocket_url": "wss://x"}
    try:
        create_agent(credentials=_CREDS, agent_config=json.dumps(raw))
    finally:
        patcher.stop()
    _, kw = transport.request.call_args
    assert kw["json_body"] == raw


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------


def test_list_agents():
    patcher, transport = _mock_transport([{"agent_id": "a1"}])
    try:
        result = list_agents(credentials=_CREDS)
    finally:
        patcher.stop()
    assert isinstance(result, list)
    transport.request.assert_called_once()


# ---------------------------------------------------------------------------
# create_phone_number
# ---------------------------------------------------------------------------


def test_create_phone_number():
    patcher, transport = _mock_transport({"phone_number": "+14155550001"})
    try:
        result = create_phone_number(
            credentials=_CREDS,
            area_code="415",
            inbound_agent_id="agent1",
        )
    finally:
        patcher.stop()
    assert result["phone_number"] == "+14155550001"
    _, kw = transport.request.call_args
    assert kw["json_body"]["area_code"] == 415
    assert kw["json_body"]["inbound_agent_id"] == "agent1"


# ---------------------------------------------------------------------------
# Registration check
# ---------------------------------------------------------------------------


def test_retell_operations_registered():
    from noodle_nodes.integrations_v2.registry import get_registered_operation
    for node_id in [
        "retell_create_call",
        "retell_get_call",
        "retell_list_calls",
        "retell_create_agent",
        "retell_list_agents",
        "retell_create_phone_number",
    ]:
        reg = get_registered_operation(node_id)
        assert reg is not None, f"{node_id} not registered"
