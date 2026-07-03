"""Azure AI Speech node — STT and TTS via azure-cognitiveservices-speech SDK.

Both modes are exposed through a single ``azure_ai_speech`` node, switchable
via the ``mode`` parameter (``"stt"`` | ``"tts"``).
"""

from __future__ import annotations

import base64
from typing import Any

from nodyra.artifacts import is_artifact_ref, write_bytes
from nodyra.artifacts import read_bytes as read_artifact_bytes
from nodyra.sdk import node
from nodyra_nodes._creds import cred_multi

AI_CATEGORY = "AI"

# Output format string → Azure SDK SpeechSynthesisOutputFormat enum name
_FORMAT_MAP: dict[str, str] = {
    "audio-16khz-128kbitrate-mono-mp3": "Audio16Khz128KBitRateMonoMp3",
    "audio-24khz-96kbitrate-mono-mp3": "Audio24Khz96KBitRateMonoMp3",
    "audio-48khz-96kbitrate-mono-mp3": "Audio48Khz96KBitRateMonoMp3",
    "riff-16khz-16bit-mono-pcm": "Riff16Khz16BitMonoPcm",
    "riff-24khz-16bit-mono-pcm": "Riff24Khz16BitMonoPcm",
    "riff-48khz-16bit-mono-pcm": "Riff48Khz16BitMonoPcm",
}

_PROFANITY_MAP: dict[str, str] = {
    "masked": "Masked",
    "removed": "Removed",
    "raw": "Raw",
}

OUTPUT_FORMAT_CHOICES = list(_FORMAT_MAP.keys())
PROFANITY_CHOICES = list(_PROFANITY_MAP.keys())


def _get_creds(credentials: Any) -> tuple[str, str]:
    """Extract (api_key, region) from a credential value."""
    if isinstance(credentials, dict):
        return str(credentials.get("api_key") or ""), str(credentials.get("region") or "")
    if isinstance(credentials, str):
        return credentials, ""
    return "", ""


def _resolve_audio_bytes(audio_input: Any) -> bytes:
    """Resolve audio input to raw bytes.

    Accepts: artifact ref dict, base64 string, or raw bytes.
    """
    if is_artifact_ref(audio_input):
        return read_artifact_bytes(audio_input)
    if isinstance(audio_input, bytes):
        return audio_input
    if isinstance(audio_input, str) and audio_input:
        try:
            return base64.b64decode(audio_input)
        except Exception as exc:
            raise ValueError(
                "azure_ai_speech STT: audio_input must be an artifact ref, "
                "raw bytes, or a base64-encoded string"
            ) from exc
    raise ValueError(
        "azure_ai_speech STT: audio_input is required and must be an artifact ref, "
        "raw bytes, or base64 string"
    )


def _run_stt(
    speechsdk: Any,
    api_key: str,
    region: str,
    audio_bytes: bytes,
    language: str,
    profanity: str,
    detailed: bool,
) -> dict[str, Any]:
    speech_config = speechsdk.SpeechConfig(subscription=api_key, region=region)
    speech_config.speech_recognition_language = language or "en-US"

    profanity_attr = _PROFANITY_MAP.get(profanity or "masked", "Masked")
    speech_config.set_profanity(getattr(speechsdk.ProfanityOption, profanity_attr))

    if detailed:
        speech_config.output_format = speechsdk.OutputFormat.Detailed

    push_stream = speechsdk.audio.PushAudioInputStream()
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

    push_stream.write(audio_bytes)
    push_stream.close()

    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        output: dict[str, Any] = {
            "text": result.text,
            "language": language,
            "reason": "RecognizedSpeech",
        }
        if detailed and hasattr(result, "json"):
            try:
                import json as _json

                detail = _json.loads(result.json)
                output["detail"] = detail
            except Exception:
                pass
        return output

    if result.reason == speechsdk.ResultReason.NoMatch:
        return {"text": "", "language": language, "reason": "NoMatch"}

    cancellation = result.cancellation_details
    raise RuntimeError(
        f"azure_ai_speech STT failed: {cancellation.reason} — {cancellation.error_details}"
    )


def _run_tts(
    speechsdk: Any,
    api_key: str,
    region: str,
    text: str,
    voice: str,
    style: str,
    output_format: str,
    ssml: bool,
) -> Any:
    speech_config = speechsdk.SpeechConfig(subscription=api_key, region=region)
    speech_config.speech_synthesis_voice_name = voice or "en-US-JennyNeural"

    fmt_name = _FORMAT_MAP.get(
        output_format or "audio-16khz-128kbitrate-mono-mp3",
        "Audio16Khz128KBitRateMonoMp3",
    )
    speech_config.set_speech_synthesis_output_format(
        getattr(speechsdk.SpeechSynthesisOutputFormat, fmt_name)
    )

    # Synthesize with no audio device (capture to bytes via result.audio_data)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

    if ssml:
        result = synthesizer.speak_ssml_async(text).get()
    elif style and style != "neutral":
        # Wrap in SSML to apply speaking style
        ssml_text = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
            f'<voice name="{voice}">'
            f'<mstts:express-as style="{style}">{_xml_escape(text)}</mstts:express-as>'
            f"</voice></speak>"
        )
        result = synthesizer.speak_ssml_async(ssml_text).get()
    else:
        result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data

    cancellation = result.cancellation_details
    raise RuntimeError(
        f"azure_ai_speech TTS failed: {cancellation.reason} — {cancellation.error_details}"
    )


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


@node(
    name="Azure AI Speech",
    id="azure_ai_speech",
    category=AI_CATEGORY,
    role="executable",
    icon="brand:azure",
    requirements=["azure-cognitiveservices-speech>=1.35"],
    inputs=["main"],
    outputs=["main"],
    param_groups={
        "STT Options": ["language", "profanity", "detailed"],
        "TTS Options": ["text", "voice", "style", "output_format", "ssml", "filename"],
    },
    params={
        "credentials": {
            **cred_multi(
                "azure_speech",
                "Azure Speech credentials",
                ["region", "api_key"],
            ),
            "description": "Azure Cognitive Services Speech credential (region + api_key).",
        },
        "mode": {
            "choices": ["stt", "tts"],
            "description": "Operation mode: 'stt' (speech-to-text) or 'tts' (text-to-speech).",
        },
        "language": {
            "placeholder": "en-US",
            "description": "[STT] Recognition language (BCP-47 tag, e.g. 'en-US', 'fr-FR').",
            "group": "STT Options",
        },
        "profanity": {
            "choices": PROFANITY_CHOICES,
            "description": "[STT] Profanity filter: masked (default), removed, or raw.",
            "group": "STT Options",
        },
        "detailed": {
            "description": "[STT] Return word-level timestamps and confidence scores.",
            "group": "STT Options",
        },
        "text": {
            "multiline": True,
            "description": "[TTS] Text to synthesize. Also accepts wired string input.",
            "group": "TTS Options",
        },
        "voice": {
            "placeholder": "en-US-JennyNeural",
            "description": "[TTS] Azure Neural voice name (e.g. 'en-US-JennyNeural').",
            "group": "TTS Options",
        },
        "style": {
            "placeholder": "neutral",
            "description": "[TTS] Speaking style (neutral, cheerful, sad, angry, excited, …). Uses SSML.",
            "group": "TTS Options",
        },
        "output_format": {
            "choices": OUTPUT_FORMAT_CHOICES,
            "description": "[TTS] Audio output format.",
            "group": "TTS Options",
        },
        "ssml": {
            "description": "[TTS] Treat 'text' as raw SSML markup.",
            "group": "TTS Options",
        },
        "filename": {
            "description": "[TTS] Artifact filename for the audio output (e.g. 'speech.mp3').",
            "group": "TTS Options",
        },
    },
)
def azure_ai_speech(
    input=None,
    credentials: Any = None,
    mode: str = "stt",
    # STT
    language: str = "en-US",
    profanity: str = "masked",
    detailed: bool = False,
    # TTS
    text: str = "",
    voice: str = "en-US-JennyNeural",
    style: str = "neutral",
    output_format: str = "audio-16khz-128kbitrate-mono-mp3",
    ssml: bool = False,
    filename: str = "",
) -> dict[str, Any]:
    """Azure Cognitive Services Speech — enterprise STT and TTS."""
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        raise ImportError(
            "azure_ai_speech requires azure-cognitiveservices-speech. "
            "Install with: pip install 'azure-cognitiveservices-speech>=1.35'"
        )

    api_key, region = _get_creds(credentials)
    if not api_key:
        raise ValueError("azure_ai_speech: credentials.api_key is required")
    if not region:
        raise ValueError("azure_ai_speech: credentials.region is required")

    if mode == "tts":
        effective_text = text or (str(input) if input is not None else "")
        if not effective_text:
            raise ValueError("azure_ai_speech TTS: text input is required")

        audio_bytes = _run_tts(
            speechsdk=speechsdk,
            api_key=api_key,
            region=region,
            text=effective_text,
            voice=voice or "en-US-JennyNeural",
            style=style or "neutral",
            output_format=output_format or "audio-16khz-128kbitrate-mono-mp3",
            ssml=bool(ssml),
        )

        ext = "mp3" if "mp3" in (output_format or "") else "wav"
        artifact_name = filename or f"azure_speech.{ext}"
        if "." not in artifact_name:
            artifact_name = f"{artifact_name}.{ext}"

        content_type = "audio/mpeg" if ext == "mp3" else "audio/wav"
        artifact = write_bytes(audio_bytes, name=artifact_name, content_type=content_type)
        return {"audio": artifact, "format": output_format, "voice": voice}

    else:  # mode == "stt"
        audio_input = input if input is not None else None
        audio_bytes = _resolve_audio_bytes(audio_input)

        return _run_stt(
            speechsdk=speechsdk,
            api_key=api_key,
            region=region,
            audio_bytes=audio_bytes,
            language=language or "en-US",
            profanity=profanity or "masked",
            detailed=bool(detailed),
        )
