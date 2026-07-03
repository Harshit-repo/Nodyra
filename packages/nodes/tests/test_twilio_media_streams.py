"""Tests for Twilio Media Streams Start node and TwilioMediaStreamAdapter."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock

import pytest

from nodyra_nodes.integrations_v2.providers.twilio.media_streams import (
    TwilioMediaStreamAdapter,
    twilio_media_streams_start,
)

_CREDS = {"account_sid": "ACtest", "auth_token": "tok_test"}

_MULAW_BYTES = b"\x7f" * 8  # 8 bytes of near-silence μ-law

_START_MSG = json.dumps(
    {
        "event": "start",
        "streamSid": "MZ123",
        "start": {"callSid": "CA456", "tracks": ["inbound"]},
    }
)

_STOP_MSG = json.dumps({"event": "stop", "streamSid": "MZ123"})

_AUDIO_MSG = json.dumps(
    {
        "event": "media",
        "streamSid": "MZ123",
        "media": {"payload": base64.b64encode(_MULAW_BYTES).decode()},
    }
)

_MARK_MSG = json.dumps(
    {"event": "mark", "streamSid": "MZ123", "mark": {"name": "end"}}
)


def _ws(messages: list[str]) -> AsyncMock:
    ws = AsyncMock()
    ws.recv = AsyncMock(side_effect=messages)
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# TwilioMediaStreamAdapter — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_start_sets_sids():
    adapter = TwilioMediaStreamAdapter(_ws([_START_MSG]))
    await adapter._wait_for_start()
    assert adapter.stream_sid == "MZ123"
    assert adapter.call_sid == "CA456"


@pytest.mark.asyncio
async def test_wait_for_start_raises_on_early_stop():
    adapter = TwilioMediaStreamAdapter(_ws([_STOP_MSG]))
    with pytest.raises(RuntimeError, match="closed before"):
        await adapter._wait_for_start()


@pytest.mark.asyncio
async def test_recv_audio_returns_bytes():
    adapter = TwilioMediaStreamAdapter(_ws([_START_MSG, _AUDIO_MSG]))
    await adapter._wait_for_start()
    data = await adapter.recv_audio()
    assert isinstance(data, bytes)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_recv_audio_skips_mark_event():
    adapter = TwilioMediaStreamAdapter(_ws([_START_MSG, _MARK_MSG, _AUDIO_MSG]))
    await adapter._wait_for_start()
    data = await adapter.recv_audio()
    assert isinstance(data, bytes)


@pytest.mark.asyncio
async def test_recv_audio_raises_stop_async_iteration():
    adapter = TwilioMediaStreamAdapter(_ws([_START_MSG, _STOP_MSG]))
    await adapter._wait_for_start()
    with pytest.raises(StopAsyncIteration):
        await adapter.recv_audio()


@pytest.mark.asyncio
async def test_send_audio_sends_media_json():
    ws = _ws([_START_MSG])
    adapter = TwilioMediaStreamAdapter(ws)
    await adapter._wait_for_start()
    await adapter.send_audio(b"\x00" * 160)
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["event"] == "media"
    assert sent["streamSid"] == "MZ123"
    assert base64.b64decode(sent["media"]["payload"])  # non-empty payload


@pytest.mark.asyncio
async def test_send_mark_sends_mark_json():
    ws = _ws([_START_MSG])
    adapter = TwilioMediaStreamAdapter(ws)
    await adapter._wait_for_start()
    await adapter.send_mark("tts_done")
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["event"] == "mark"
    assert sent["mark"]["name"] == "tts_done"


@pytest.mark.asyncio
async def test_close_calls_ws_close():
    ws = _ws([_START_MSG])
    adapter = TwilioMediaStreamAdapter(ws)
    await adapter.close()
    ws.close.assert_called_once()


# ---------------------------------------------------------------------------
# Node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_raises_without_ws_runtime():
    from nodyra.context import node_ws_connect

    assert node_ws_connect.get() is None
    with pytest.raises(RuntimeError, match="no WebSocket runtime"):
        await twilio_media_streams_start(credentials=_CREDS, stream_url="wss://x.test/ws")


@pytest.mark.asyncio
async def test_node_connects_and_returns_adapter():
    from nodyra.context import node_ws_connect

    ws = _ws([_START_MSG])
    connect_fn = AsyncMock(return_value=ws)
    token = node_ws_connect.set(connect_fn)
    try:
        adapter = await twilio_media_streams_start(
            credentials=_CREDS,
            stream_url="wss://media.twilio.com/v1/Streams/CA456",
        )
    finally:
        node_ws_connect.reset(token)

    assert isinstance(adapter, TwilioMediaStreamAdapter)
    assert adapter.stream_sid == "MZ123"
    assert adapter.call_sid == "CA456"
    connect_fn.assert_awaited_once()
    url, params = connect_fn.call_args[0]
    assert url == "wss://media.twilio.com/v1/Streams/CA456"


@pytest.mark.asyncio
async def test_node_builds_url_from_call_sid():
    from nodyra.context import node_ws_connect

    ws = _ws([_START_MSG])
    connect_fn = AsyncMock(return_value=ws)
    token = node_ws_connect.set(connect_fn)
    try:
        await twilio_media_streams_start(credentials=_CREDS, call_sid="CA789")
    finally:
        node_ws_connect.reset(token)

    url = connect_fn.call_args[0][0]
    assert "CA789" in url


@pytest.mark.asyncio
async def test_node_raises_with_no_url_or_call_sid():
    from nodyra.context import node_ws_connect

    connect_fn = AsyncMock()
    token = node_ws_connect.set(connect_fn)
    try:
        with pytest.raises(ValueError, match="stream_url or call_sid"):
            await twilio_media_streams_start(credentials=_CREDS)
    finally:
        node_ws_connect.reset(token)
