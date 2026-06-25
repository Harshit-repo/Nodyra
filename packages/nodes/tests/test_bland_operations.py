"""Tests for Bland AI v2 operations."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from noodle_nodes.integrations_v2.providers.bland.operations import (
    analyze_call,
    get_call,
    list_calls,
    send_call,
    stop_call,
)

_CREDS = {"api_key": "bland_test_key"}
_TRANSPORT_PATH = "noodle_nodes.integrations_v2.providers.bland.operations._transport"


def _mock_transport(return_value: object = None) -> tuple[MagicMock, MagicMock]:
    patcher = patch(_TRANSPORT_PATH)
    mock_fn = patcher.start()
    transport = MagicMock()
    mock_fn.return_value = transport
    transport.request.return_value = return_value or {}
    return patcher, transport


def test_send_call():
    patcher, transport = _mock_transport({"call_id": "call1", "status": "queued"})
    try:
        result = send_call(
            credentials=_CREDS,
            phone_number="+15005551234",
            task="Remind them about the appointment.",
        )
    finally:
        patcher.stop()
    assert result["call_id"] == "call1"
    _, kw = transport.request.call_args
    assert kw["json_body"]["phone_number"] == "+15005551234"
    assert kw["json_body"]["task"] == "Remind them about the appointment."


def test_send_call_includes_optional_fields():
    patcher, transport = _mock_transport({"call_id": "call2"})
    try:
        send_call(
            credentials=_CREDS,
            phone_number="+15005551234",
            task="Hello",
            from_number="+15005550006",
            webhook="https://example.com/hook",
        )
    finally:
        patcher.stop()
    _, kw = transport.request.call_args
    assert kw["json_body"]["from"] == "+15005550006"
    assert kw["json_body"]["webhook"] == "https://example.com/hook"


def test_send_call_raises_without_required_fields():
    with pytest.raises(ValueError, match="phone_number"):
        send_call(credentials=_CREDS, phone_number="", task="Hello")
    with pytest.raises(ValueError, match="task"):
        send_call(credentials=_CREDS, phone_number="+1555", task="")


def test_get_call():
    patcher, transport = _mock_transport({"call_id": "call1", "status": "completed"})
    try:
        result = get_call(credentials=_CREDS, call_id="call1")
    finally:
        patcher.stop()
    assert result["status"] == "completed"
    args, _ = transport.request.call_args
    assert "call1" in args[1]


def test_list_calls():
    patcher, transport = _mock_transport({"calls": [{"call_id": "c1"}]})
    try:
        list_calls(credentials=_CREDS, limit=10)
    finally:
        patcher.stop()
    _, kw = transport.request.call_args
    assert kw["params"]["limit"] == 10


def test_stop_call():
    patcher, transport = _mock_transport({"status": "stopped"})
    try:
        stop_call(credentials=_CREDS, call_id="call1")
    finally:
        patcher.stop()
    args, _ = transport.request.call_args
    assert "call1" in args[1]
    assert "stop" in args[1]


def test_analyze_call():
    patcher, transport = _mock_transport({"answers": [True]})
    questions = json.dumps([["Did they agree?", "boolean"]])
    try:
        result = analyze_call(
            credentials=_CREDS,
            call_id="call1",
            goal="Schedule an appointment",
            questions=questions,
        )
    finally:
        patcher.stop()
    assert result["answers"] == [True]
    _, kw = transport.request.call_args
    assert kw["json_body"]["goal"] == "Schedule an appointment"
    assert isinstance(kw["json_body"]["questions"], list)


def test_bland_operations_registered():
    from noodle_nodes.integrations_v2.registry import get_registered_operation
    for node_id in [
        "bland_send_call",
        "bland_get_call",
        "bland_list_calls",
        "bland_stop_call",
        "bland_analyze_call",
    ]:
        assert get_registered_operation(node_id) is not None, f"{node_id} not registered"
