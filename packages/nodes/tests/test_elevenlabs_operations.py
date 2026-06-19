"""Tests for ElevenLabs ConvAI operations and elevenlabs_tts node."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from noodle_nodes.integrations_v2.providers.elevenlabs_convai.operations import (
    create_agent,
    get_agent,
    get_conversation,
    get_signed_url,
    list_agents,
    list_conversations,
)
from noodle_nodes.ai_extra import elevenlabs_tts

_CREDS = {"api_key": "el_test_key"}
_TRANSPORT_PATH = (
    "noodle_nodes.integrations_v2.providers.elevenlabs_convai.operations._transport"
)


def _mock_transport(return_value: object = None) -> tuple[MagicMock, MagicMock]:
    patcher = patch(_TRANSPORT_PATH)
    mock_fn = patcher.start()
    transport = MagicMock()
    mock_fn.return_value = transport
    transport.request.return_value = return_value or {}
    return patcher, transport


# ---------------------------------------------------------------------------
# ConvAI operations
# ---------------------------------------------------------------------------


def test_create_agent():
    patcher, transport = _mock_transport({"agent_id": "ag1"})
    try:
        result = create_agent(
            credentials=_CREDS,
            name="MyAgent",
            first_message="Hello!",
            system_prompt="Be helpful.",
            voice_id="voice1",
        )
    finally:
        patcher.stop()
    assert result["agent_id"] == "ag1"
    _, kw = transport.request.call_args
    body = kw["json_body"]
    assert body["name"] == "MyAgent"
    assert body["conversation_config"]["first_message"] == "Hello!"


def test_create_agent_with_raw_config():
    patcher, transport = _mock_transport({"agent_id": "ag2"})
    raw = {"name": "raw", "conversation_config": {}}
    try:
        create_agent(credentials=_CREDS, agent_config=json.dumps(raw))
    finally:
        patcher.stop()
    _, kw = transport.request.call_args
    assert kw["json_body"] == raw


def test_get_agent():
    patcher, transport = _mock_transport({"agent_id": "ag1", "name": "Bob"})
    try:
        result = get_agent(credentials=_CREDS, agent_id="ag1")
    finally:
        patcher.stop()
    assert result["name"] == "Bob"
    args, _ = transport.request.call_args
    assert "ag1" in args[1]


def test_list_agents():
    patcher, transport = _mock_transport({"agents": []})
    try:
        list_agents(credentials=_CREDS, page_size=10)
    finally:
        patcher.stop()
    _, kw = transport.request.call_args
    assert kw["params"]["page_size"] == 10


def test_get_conversation():
    patcher, transport = _mock_transport({"conversation_id": "conv1"})
    try:
        result = get_conversation(credentials=_CREDS, conversation_id="conv1")
    finally:
        patcher.stop()
    assert result["conversation_id"] == "conv1"


def test_list_conversations_with_agent_filter():
    patcher, transport = _mock_transport({"conversations": []})
    try:
        list_conversations(credentials=_CREDS, agent_id="ag1", page_size=5)
    finally:
        patcher.stop()
    _, kw = transport.request.call_args
    assert kw["params"]["agent_id"] == "ag1"
    assert kw["params"]["page_size"] == 5


def test_get_signed_url():
    patcher, transport = _mock_transport({"signed_url": "wss://example.com/ws?t=xyz"})
    try:
        result = get_signed_url(credentials=_CREDS, agent_id="ag1")
    finally:
        patcher.stop()
    assert "signed_url" in result
    _, kw = transport.request.call_args
    assert kw["params"]["agent_id"] == "ag1"


# ---------------------------------------------------------------------------
# elevenlabs_tts node
# ---------------------------------------------------------------------------


def test_elevenlabs_tts_returns_artifact():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"fake_audio_bytes"
    mock_response.headers = {"content-type": "audio/mpeg"}

    fake_artifact = {"__noodle_artifact__": True, "artifact_id": "abc"}

    with patch("noodle_nodes.ai_extra.requests.post", return_value=mock_response) as mock_post, \
         patch("noodle_nodes.ai_extra.write_bytes", return_value=fake_artifact) as mock_wb:
        result = elevenlabs_tts(
            credentials="el_key",
            text="Hello world",
            voice_id="test_voice",
        )

    assert result == fake_artifact
    mock_post.assert_called_once()
    call_json = mock_post.call_args.kwargs["json"]
    assert call_json["text"] == "Hello world"
    assert call_json["model_id"] == "eleven_multilingual_v2"
    mock_wb.assert_called_once_with(
        b"fake_audio_bytes",
        name="speech.mp3",
        content_type="audio/mpeg",
        kind="audio",
        metadata={"model": "eleven_multilingual_v2", "voice_id": "test_voice"},
    )


def test_elevenlabs_tts_raises_on_http_error():
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    with patch("noodle_nodes.ai_extra.requests.post", return_value=mock_response):
        with pytest.raises(RuntimeError, match="HTTP 401"):
            elevenlabs_tts(credentials="bad_key", text="Hello", voice_id="v1")


def test_elevenlabs_convai_operations_registered():
    from noodle_nodes.integrations_v2.registry import get_registered_operation
    for node_id in [
        "elevenlabs_create_agent",
        "elevenlabs_get_agent",
        "elevenlabs_list_agents",
        "elevenlabs_get_conversation",
        "elevenlabs_list_conversations",
        "elevenlabs_get_signed_url",
    ]:
        assert get_registered_operation(node_id) is not None, f"{node_id} not registered"
