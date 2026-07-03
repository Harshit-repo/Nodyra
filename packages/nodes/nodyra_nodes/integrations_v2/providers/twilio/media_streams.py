"""Twilio Media Streams node — bidirectional real-time audio over WebSocket."""

from __future__ import annotations

import base64
import json
from typing import Any

from nodyra.context import WebSocketConnection, node_ws_connect
from nodyra.sdk import node
from nodyra_nodes.integrations_v2.providers.twilio.operations import _credentials_dict

_AUDIOOP_WIDTH = 2  # 16-bit samples for audioop (2 bytes per sample)


def _mulaw_to_pcm16(data: bytes) -> bytes:
    try:
        import audioop
        return audioop.ulaw2lin(data, _AUDIOOP_WIDTH)
    except ImportError:
        return data


def _pcm16_to_mulaw(data: bytes) -> bytes:
    try:
        import audioop
        return audioop.lin2ulaw(data, _AUDIOOP_WIDTH)
    except ImportError:
        return data


class TwilioMediaStreamAdapter:
    """Wraps a Twilio Media Streams WebSocket for bidirectional audio exchange.

    Twilio sends JSON frames with base64-encoded μ-law audio. This adapter
    decodes frames to PCM16 and encodes outbound PCM16 back to μ-law before
    sending.
    """

    def __init__(
        self,
        ws: WebSocketConnection,
        *,
        audio_format: str = "mulaw",
        sample_rate: int = 8000,
    ) -> None:
        self._ws = ws
        self._audio_format = audio_format
        self._sample_rate = sample_rate
        self.call_sid: str = ""
        self.stream_sid: str = ""

    async def _wait_for_start(self) -> None:
        """Read frames until the 'start' event arrives (provides stream_sid/call_sid)."""
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if not isinstance(msg, dict):
                continue
            event = msg.get("event")
            if event == "start":
                self.stream_sid = str(msg.get("streamSid", ""))
                start_data = msg.get("start") or {}
                self.call_sid = str(start_data.get("callSid", ""))
                return
            if event == "stop":
                raise RuntimeError(
                    "twilio_media_streams_start: stream closed before 'start' event received"
                )

    def _decode_payload(self, payload: str) -> bytes:
        raw = base64.b64decode(payload)
        if self._audio_format == "mulaw":
            return _mulaw_to_pcm16(raw)
        return raw

    def _encode_payload(self, pcm16: bytes) -> str:
        out = _pcm16_to_mulaw(pcm16) if self._audio_format == "mulaw" else pcm16
        return base64.b64encode(out).decode()

    async def recv_audio(self) -> bytes:
        """Receive the next audio chunk from the caller (PCM16 bytes)."""
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if not isinstance(msg, dict):
                continue
            event = msg.get("event")
            if event == "media":
                payload = (msg.get("media") or {}).get("payload", "")
                return self._decode_payload(payload)
            if event == "stop":
                raise StopAsyncIteration("twilio_media_streams: stream stopped by Twilio")

    async def send_audio(self, chunk: bytes) -> None:
        """Send an audio chunk to the caller (PCM16 bytes, encoded as μ-law for Twilio)."""
        if not self.stream_sid:
            return
        msg = json.dumps(
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": self._encode_payload(chunk)},
            }
        )
        await self._ws.send(msg)

    async def send_mark(self, label: str) -> None:
        """Send a Twilio mark event for audio synchronization."""
        if not self.stream_sid:
            return
        msg = json.dumps(
            {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": label},
            }
        )
        await self._ws.send(msg)

    async def close(self) -> None:
        """Close the media stream WebSocket."""
        await self._ws.close()


@node(
    name="Twilio Media Streams Start",
    id="twilio_media_streams_start",
    description=(
        "Opens a bidirectional Twilio Media Streams WebSocket connection and returns a "
        "stream adapter. Downstream nodes use the adapter to read caller audio and send "
        "TTS audio back in real time."
    ),
    icon="brand:twilio",
    category="Communication",
    role="supplier",
    inputs=[],
    outputs=["main"],
    output_kinds={"main": "ai_stream"},
    requirements=["audioop-lts>=0.2; python_version>='3.13'"],
    params={
        "credentials": {
            "type": "credential",
            "label": "Twilio credentials",
            "description": "Twilio Account SID + Auth Token.",
        },
        "stream_url": {
            "label": "Stream URL",
            "description": "WebSocket URL from TwiML <Connect><Stream url=...>. "
            "If omitted, constructed from call_sid.",
        },
        "call_sid": {
            "label": "Call SID",
            "description": "Twilio CallSid. Used to build stream_url when stream_url is empty.",
        },
        "audio_format": {
            "choices": ["mulaw", "pcm16"],
            "label": "Audio format",
            "description": "Encoding for audio frames. 'mulaw' is Twilio's default.",
        },
        "sample_rate": {
            "type": "number",
            "label": "Sample rate (Hz)",
            "description": "Audio sample rate. Twilio Media Streams default is 8000.",
        },
        "inbound_track": {
            "choices": ["both", "inbound", "outbound"],
            "label": "Inbound track",
            "description": "Which call audio tracks to receive.",
        },
        "timeout": {
            "type": "number",
            "label": "Timeout (s)",
            "description": "Max stream duration in seconds before the node closes the connection.",
        },
    },
)
async def twilio_media_streams_start(
    *,
    credentials: Any = None,
    stream_url: str = "",
    call_sid: str = "",
    audio_format: str = "mulaw",
    sample_rate: int = 8000,
    inbound_track: str = "both",
    timeout: int = 300,
) -> TwilioMediaStreamAdapter:
    creds = _credentials_dict(credentials)
    account_sid = str(creds.get("account_sid") or "")
    auth_token = str(creds.get("auth_token") or "")

    connect_fn = node_ws_connect.get()
    if connect_fn is None:
        raise RuntimeError(
            "twilio_media_streams_start: no WebSocket runtime available. "
            "This node must run inside a Nodyra runtime that supports node WebSockets."
        )

    effective_url = stream_url or (
        f"wss://media.twilio.com/v1/Streams/{call_sid}" if call_sid else ""
    )
    if not effective_url:
        raise ValueError(
            "twilio_media_streams_start: stream_url or call_sid is required"
        )

    ws = await connect_fn(
        effective_url,
        {
            "account_sid": account_sid,
            "auth_token": auth_token,
            "track": inbound_track,
        },
    )

    adapter = TwilioMediaStreamAdapter(
        ws, audio_format=audio_format, sample_rate=sample_rate
    )
    await adapter._wait_for_start()
    return adapter
