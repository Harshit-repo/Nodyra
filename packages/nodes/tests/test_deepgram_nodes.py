"""Tests for Deepgram Realtime STT node."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nodyra_nodes.deepgram_nodes import deepgram_realtime_stt


class _MockAdapter:
    """Fake media stream adapter that yields N audio chunks then stops."""

    _audio_format = "mulaw"
    _sample_rate = 8000

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self._i = 0

    async def recv_audio(self) -> bytes:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration("stream ended")
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk


def _build_deepgram_mock(started: bool = True) -> tuple[MagicMock, MagicMock]:
    """Return (mock_deepgram_module, mock_connection)."""
    mock_module = MagicMock()
    mock_connection = MagicMock()
    mock_connection.start = AsyncMock(return_value=started)
    mock_connection.send = AsyncMock()
    mock_connection.finish = AsyncMock()

    registered: dict[Any, Any] = {}

    def _on(event: Any, cb: Any) -> None:
        registered[event] = cb

    mock_connection.on = _on
    mock_connection._registered = registered

    mock_live = MagicMock()
    mock_live.asynclive.v.return_value = mock_connection
    mock_module.DeepgramClient.return_value = MagicMock(listen=mock_live)
    mock_module.LiveTranscriptionEvents.Transcript = "Transcript"
    mock_module.LiveOptions = MagicMock(side_effect=lambda **kw: MagicMock(**kw))

    return mock_module, mock_connection


def _patch_deepgram(mock_module: MagicMock):
    return patch.dict(
        sys.modules,
        {
            "deepgram": mock_module,
            "deepgram.audio": MagicMock(),
            "deepgram.audio.speaker": MagicMock(),
        },
    )


# ---------------------------------------------------------------------------
# Error / validation paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raises_without_adapter():
    mock_module, _ = _build_deepgram_mock()
    with _patch_deepgram(mock_module):
        with pytest.raises(ValueError, match="input.*adapter.*required"):
            await deepgram_realtime_stt(credentials="dg_key")


@pytest.mark.asyncio
async def test_raises_without_credentials():
    adapter = _MockAdapter([])
    mock_module, _ = _build_deepgram_mock()
    with _patch_deepgram(mock_module):
        with pytest.raises(ValueError, match="credentials.*required"):
            await deepgram_realtime_stt(input=adapter, credentials="")


@pytest.mark.asyncio
async def test_raises_when_deepgram_not_installed():
    adapter = _MockAdapter([])
    with patch.dict(sys.modules, {"deepgram": None}):
        with pytest.raises(ImportError, match="deepgram-sdk"):
            await deepgram_realtime_stt(input=adapter, credentials="dg_key")


@pytest.mark.asyncio
async def test_raises_when_connection_fails_to_start():
    adapter = _MockAdapter([])
    mock_module, _ = _build_deepgram_mock(started=False)
    with _patch_deepgram(mock_module):
        with pytest.raises(RuntimeError, match="failed to open"):
            await deepgram_realtime_stt(input=adapter, credentials="dg_key")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sends_all_chunks_to_deepgram():
    chunks = [b"\x7f" * 160] * 3
    adapter = _MockAdapter(chunks)
    mock_module, connection = _build_deepgram_mock()
    with _patch_deepgram(mock_module):
        await deepgram_realtime_stt(input=adapter, credentials="dg_key")
    assert connection.send.await_count == 3
    connection.start.assert_awaited_once()
    connection.finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_returns_interim_and_final_keys():
    adapter = _MockAdapter([])
    mock_module, _ = _build_deepgram_mock()
    with _patch_deepgram(mock_module):
        result = await deepgram_realtime_stt(input=adapter, credentials="dg_key")
    assert "interim" in result
    assert "final" in result
    assert isinstance(result["interim"], list)
    assert isinstance(result["final"], str)


@pytest.mark.asyncio
async def test_callback_accumulates_final_transcripts():
    adapter = _MockAdapter([b"\x00"] * 2)
    mock_module, connection = _build_deepgram_mock()
    with _patch_deepgram(mock_module):
        # Run the node — this registers the callback
        # We can call the registered callback directly after start
        orig_start = connection.start

        async def patched_start(options: Any) -> bool:
            result = await orig_start(options)
            # Simulate two final transcript events fired by the SDK
            cb = connection._registered.get("Transcript")
            if cb:
                mock_result_1 = MagicMock()
                mock_result_1.channel.alternatives = [MagicMock(transcript="Hello")]
                mock_result_1.is_final = True
                await cb(mock_result_1)

                mock_result_2 = MagicMock()
                mock_result_2.channel.alternatives = [MagicMock(transcript="World")]
                mock_result_2.is_final = True
                await cb(mock_result_2)
            return result

        connection.start = patched_start
        result = await deepgram_realtime_stt(input=adapter, credentials="dg_key")

    assert result["final"] == "Hello World"


@pytest.mark.asyncio
async def test_callback_accumulates_interim_transcripts():
    adapter = _MockAdapter([b"\x00"])
    mock_module, connection = _build_deepgram_mock()
    with _patch_deepgram(mock_module):
        orig_start = connection.start

        async def patched_start(options: Any) -> bool:
            result = await orig_start(options)
            cb = connection._registered.get("Transcript")
            if cb:
                mock_result = MagicMock()
                mock_result.channel.alternatives = [MagicMock(transcript="partial")]
                mock_result.is_final = False
                await cb(mock_result)
            return result

        connection.start = patched_start
        result = await deepgram_realtime_stt(input=adapter, credentials="dg_key")

    assert "partial" in result["interim"]


@pytest.mark.asyncio
async def test_callback_skips_empty_transcripts():
    adapter = _MockAdapter([])
    mock_module, connection = _build_deepgram_mock()
    with _patch_deepgram(mock_module):
        orig_start = connection.start

        async def patched_start(options: Any) -> bool:
            result = await orig_start(options)
            cb = connection._registered.get("Transcript")
            if cb:
                mock_result = MagicMock()
                mock_result.channel.alternatives = [MagicMock(transcript="")]
                mock_result.is_final = True
                await cb(mock_result)
            return result

        connection.start = patched_start
        result = await deepgram_realtime_stt(input=adapter, credentials="dg_key")

    assert result["final"] == ""
    assert result["interim"] == []


@pytest.mark.asyncio
async def test_dict_credentials_extracted():
    adapter = _MockAdapter([])
    mock_module, _ = _build_deepgram_mock()
    with _patch_deepgram(mock_module):
        result = await deepgram_realtime_stt(
            input=adapter,
            credentials={"api_key": "dg_dict_key"},
        )
    assert "final" in result
    mock_module.DeepgramClient.assert_called_once_with("dg_dict_key")
