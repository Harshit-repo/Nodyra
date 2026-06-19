"""Deepgram speech-to-text nodes."""

from __future__ import annotations

from typing import Any

from noodle.sdk import node


@node(
    name="Deepgram Realtime STT",
    id="deepgram_realtime_stt",
    description=(
        "Transcribes real-time audio from a media stream adapter using Deepgram's "
        "streaming speech-to-text API. Reads audio chunks from the upstream stream "
        "supplier and outputs collected interim and final transcripts when the stream ends."
    ),
    icon="brand:deepgram",
    category="AI",
    role="executable",
    inputs=["input"],
    input_kinds={"input": "ai_stream"},
    outputs=["interim", "final"],
    requirements=["deepgram-sdk>=3.0"],
    params={
        "credentials": {
            "type": "credential",
            "label": "Deepgram API key",
            "description": "Deepgram API key for streaming speech recognition.",
        },
        "model": {
            "choices": ["nova-2", "nova", "enhanced", "base"],
            "label": "Model",
            "description": "Deepgram transcription model.",
        },
        "language": {
            "label": "Language",
            "description": "BCP-47 language tag (e.g. en-US, fr, de).",
        },
        "interim_results": {
            "type": "boolean",
            "label": "Emit interim results",
            "description": "Collect partial transcriptions before utterances are finalized.",
        },
        "smart_format": {
            "type": "boolean",
            "label": "Smart formatting",
            "description": "Auto-format numbers, dates, punctuation.",
        },
        "diarize": {
            "type": "boolean",
            "label": "Speaker diarization",
            "description": "Identify individual speakers in the transcript.",
        },
        "utterance_end_ms": {
            "type": "number",
            "label": "Utterance end (ms)",
            "description": "Milliseconds of silence that marks the end of an utterance.",
        },
    },
)
async def deepgram_realtime_stt(
    *,
    input: Any = None,
    credentials: Any = None,
    model: str = "nova-2",
    language: str = "en-US",
    interim_results: bool = True,
    smart_format: bool = True,
    diarize: bool = False,
    utterance_end_ms: int = 1000,
) -> dict[str, Any]:
    try:
        from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents
    except ImportError as exc:
        raise ImportError(
            "deepgram-sdk>=3.0 is required for deepgram_realtime_stt. "
            "Install it with: pip install 'deepgram-sdk>=3.0'"
        ) from exc

    adapter = input
    if adapter is None:
        raise ValueError("deepgram_realtime_stt: input (media stream adapter) is required")

    api_key: str = (
        credentials
        if isinstance(credentials, str)
        else str((credentials or {}).get("api_key", ""))
        if isinstance(credentials, dict)
        else ""
    )
    if not api_key:
        raise ValueError("deepgram_realtime_stt: credentials (api_key) is required")

    audio_format: str = getattr(adapter, "_audio_format", "mulaw")
    sample_rate: int = int(getattr(adapter, "_sample_rate", 8000))
    encoding = "mulaw" if audio_format == "mulaw" else "linear16"

    dg_client = DeepgramClient(api_key)
    connection = dg_client.listen.asynclive.v("1")

    interim_transcripts: list[str] = []
    final_transcripts: list[str] = []

    async def _on_transcript(self_or_result: Any, result: Any = None, **kwargs: Any) -> None:
        r = result if result is not None else self_or_result
        try:
            text = r.channel.alternatives[0].transcript
        except (AttributeError, IndexError):
            return
        if not text:
            return
        if getattr(r, "is_final", False):
            final_transcripts.append(text)
        else:
            interim_transcripts.append(text)

    connection.on(LiveTranscriptionEvents.Transcript, _on_transcript)

    options = LiveOptions(
        model=model,
        language=language,
        encoding=encoding,
        sample_rate=sample_rate,
        interim_results=interim_results,
        smart_format=smart_format,
        diarize=diarize,
        utterance_end_ms=str(utterance_end_ms),
    )

    started = await connection.start(options)
    if not started:
        raise RuntimeError(
            "deepgram_realtime_stt: failed to open Deepgram streaming connection"
        )

    try:
        while True:
            chunk = await adapter.recv_audio()
            await connection.send(chunk)
    except StopAsyncIteration:
        pass

    await connection.finish()
    return {
        "interim": interim_transcripts,
        "final": " ".join(final_transcripts),
    }
