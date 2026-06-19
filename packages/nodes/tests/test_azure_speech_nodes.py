"""Tests for Task 21: Azure AI Speech node (STT + TTS)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from noodle_nodes.azure_speech_nodes import (
    _get_creds,
    _resolve_audio_bytes,
    _xml_escape,
    azure_ai_speech,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_speechsdk(stt_text="Hello Azure", tts_audio=b"audio_bytes") -> MagicMock:
    """Build a MagicMock that mimics azure.cognitiveservices.speech."""
    sdk = MagicMock()

    # Enums
    sdk.ResultReason.RecognizedSpeech = "RecognizedSpeech"
    sdk.ResultReason.NoMatch = "NoMatch"
    sdk.ResultReason.SynthesizingAudioCompleted = "SynthesizingAudioCompleted"
    sdk.ProfanityOption.Masked = "Masked"
    sdk.ProfanityOption.Removed = "Removed"
    sdk.ProfanityOption.Raw = "Raw"
    sdk.OutputFormat.Detailed = "Detailed"

    # SpeechSynthesisOutputFormat enum attributes
    for fmt in [
        "Audio16Khz128KBitRateMonoMp3",
        "Audio24Khz96KBitRateMonoMp3",
        "Riff16Khz16BitMonoPcm",
    ]:
        setattr(sdk.SpeechSynthesisOutputFormat, fmt, fmt)

    # SpeechConfig
    speech_config = MagicMock()
    sdk.SpeechConfig.return_value = speech_config

    # STT: PushAudioInputStream + AudioConfig + SpeechRecognizer
    push_stream = MagicMock()
    sdk.audio.PushAudioInputStream.return_value = push_stream
    sdk.audio.AudioConfig.return_value = MagicMock()

    stt_result = MagicMock()
    stt_result.reason = "RecognizedSpeech"
    stt_result.text = stt_text

    recognizer = MagicMock()
    recognizer.recognize_once_async.return_value.get.return_value = stt_result
    sdk.SpeechRecognizer.return_value = recognizer

    # TTS: SpeechSynthesizer
    tts_result = MagicMock()
    tts_result.reason = "SynthesizingAudioCompleted"
    tts_result.audio_data = tts_audio

    synthesizer = MagicMock()
    synthesizer.speak_text_async.return_value.get.return_value = tts_result
    synthesizer.speak_ssml_async.return_value.get.return_value = tts_result
    sdk.SpeechSynthesizer.return_value = synthesizer

    return sdk


def _patch_sdk(sdk: MagicMock):
    """Patch azure.cognitiveservices.speech with the given mock."""
    azure_mock = MagicMock()
    azure_mock.cognitiveservices = MagicMock()
    azure_mock.cognitiveservices.speech = sdk
    return patch.dict(
        sys.modules,
        {
            "azure": azure_mock,
            "azure.cognitiveservices": azure_mock.cognitiveservices,
            "azure.cognitiveservices.speech": sdk,
        },
    )


CREDS = {"api_key": "test-key", "region": "eastus"}


# ---------------------------------------------------------------------------
# _get_creds / _resolve_audio_bytes / _xml_escape helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_get_creds_from_dict(self):
        key, region = _get_creds({"api_key": "k", "region": "westus"})
        assert key == "k"
        assert region == "westus"

    def test_get_creds_from_string(self):
        key, region = _get_creds("my-key")
        assert key == "my-key"
        assert region == ""

    def test_get_creds_none(self):
        key, region = _get_creds(None)
        assert key == ""
        assert region == ""

    def test_resolve_audio_bytes_raw(self):
        assert _resolve_audio_bytes(b"PCM") == b"PCM"

    def test_resolve_audio_bytes_base64(self):
        import base64
        encoded = base64.b64encode(b"audio").decode()  # string, not bytes
        result = _resolve_audio_bytes(encoded)
        assert result == b"audio"

    def test_resolve_audio_bytes_invalid_raises(self):
        with pytest.raises(ValueError, match="audio_input is required"):
            _resolve_audio_bytes(None)

    def test_xml_escape(self):
        assert _xml_escape("Hello & <World>") == "Hello &amp; &lt;World&gt;"


# ---------------------------------------------------------------------------
# STT mode
# ---------------------------------------------------------------------------


class TestAzureSTT:
    def test_stt_returns_text(self):
        sdk = _make_speechsdk(stt_text="Good morning")
        with _patch_sdk(sdk):
            result = azure_ai_speech(
                input=b"audio_pcm",
                credentials=CREDS,
                mode="stt",
                language="en-US",
            )
        assert result["text"] == "Good morning"
        assert result["reason"] == "RecognizedSpeech"

    def test_stt_writes_audio_to_push_stream(self):
        sdk = _make_speechsdk()
        push_stream = sdk.audio.PushAudioInputStream.return_value

        with _patch_sdk(sdk):
            azure_ai_speech(input=b"my_audio", credentials=CREDS, mode="stt")

        push_stream.write.assert_called_once_with(b"my_audio")
        push_stream.close.assert_called_once()

    def test_stt_profanity_option_applied(self):
        sdk = _make_speechsdk()
        speech_config = sdk.SpeechConfig.return_value

        with _patch_sdk(sdk):
            azure_ai_speech(
                input=b"audio",
                credentials=CREDS,
                mode="stt",
                profanity="removed",
            )

        speech_config.set_profanity.assert_called_once_with("Removed")

    def test_stt_no_match_returns_empty_text(self):
        sdk = _make_speechsdk()
        sdk.SpeechRecognizer.return_value.recognize_once_async.return_value.get.return_value.reason = "NoMatch"

        with _patch_sdk(sdk):
            result = azure_ai_speech(input=b"silence", credentials=CREDS, mode="stt")

        assert result["text"] == ""
        assert result["reason"] == "NoMatch"

    def test_stt_missing_api_key_raises(self):
        sdk = _make_speechsdk()
        with _patch_sdk(sdk):
            with pytest.raises(ValueError, match="api_key"):
                azure_ai_speech(
                    input=b"audio",
                    credentials={"api_key": "", "region": "eastus"},
                    mode="stt",
                )

    def test_stt_missing_region_raises(self):
        sdk = _make_speechsdk()
        with _patch_sdk(sdk):
            with pytest.raises(ValueError, match="region"):
                azure_ai_speech(
                    input=b"audio",
                    credentials={"api_key": "key", "region": ""},
                    mode="stt",
                )

    def test_import_error_raised_without_azure_sdk(self):
        with patch.dict(sys.modules, {"azure.cognitiveservices.speech": None}):
            with pytest.raises((ImportError, Exception)):
                azure_ai_speech(input=b"audio", credentials=CREDS, mode="stt")


# ---------------------------------------------------------------------------
# TTS mode
# ---------------------------------------------------------------------------


class TestAzureTTS:
    def test_tts_returns_artifact(self):
        sdk = _make_speechsdk(tts_audio=b"mp3_data")

        mock_write_bytes = MagicMock(return_value={"kind": "audio", "name": "azure_speech.mp3"})
        with _patch_sdk(sdk):
            with patch("noodle_nodes.azure_speech_nodes.write_bytes", mock_write_bytes):
                result = azure_ai_speech(
                    credentials=CREDS,
                    mode="tts",
                    text="Hello world",
                    voice="en-US-JennyNeural",
                )

        mock_write_bytes.assert_called_once()
        assert result["voice"] == "en-US-JennyNeural"

    def test_tts_speak_text_called_without_style(self):
        sdk = _make_speechsdk()
        synthesizer = sdk.SpeechSynthesizer.return_value

        mock_write_bytes = MagicMock(return_value={"kind": "audio"})
        with _patch_sdk(sdk):
            with patch("noodle_nodes.azure_speech_nodes.write_bytes", mock_write_bytes):
                azure_ai_speech(
                    credentials=CREDS,
                    mode="tts",
                    text="Hello",
                    style="neutral",
                )

        synthesizer.speak_text_async.assert_called_once_with("Hello")
        synthesizer.speak_ssml_async.assert_not_called()

    def test_tts_style_uses_ssml(self):
        sdk = _make_speechsdk()
        synthesizer = sdk.SpeechSynthesizer.return_value

        mock_write_bytes = MagicMock(return_value={"kind": "audio"})
        with _patch_sdk(sdk):
            with patch("noodle_nodes.azure_speech_nodes.write_bytes", mock_write_bytes):
                azure_ai_speech(
                    credentials=CREDS,
                    mode="tts",
                    text="Hello",
                    voice="en-US-JennyNeural",
                    style="cheerful",
                )

        synthesizer.speak_ssml_async.assert_called_once()
        ssml_arg = synthesizer.speak_ssml_async.call_args[0][0]
        assert "express-as" in ssml_arg
        assert 'style="cheerful"' in ssml_arg

    def test_tts_ssml_mode(self):
        sdk = _make_speechsdk()
        synthesizer = sdk.SpeechSynthesizer.return_value
        ssml_text = '<speak version="1.0">Hi</speak>'

        mock_write_bytes = MagicMock(return_value={"kind": "audio"})
        with _patch_sdk(sdk):
            with patch("noodle_nodes.azure_speech_nodes.write_bytes", mock_write_bytes):
                azure_ai_speech(
                    credentials=CREDS,
                    mode="tts",
                    text=ssml_text,
                    ssml=True,
                )

        synthesizer.speak_ssml_async.assert_called_once_with(ssml_text)

    def test_tts_output_format_set(self):
        sdk = _make_speechsdk()
        speech_config = sdk.SpeechConfig.return_value

        mock_write_bytes = MagicMock(return_value={"kind": "audio"})
        with _patch_sdk(sdk):
            with patch("noodle_nodes.azure_speech_nodes.write_bytes", mock_write_bytes):
                azure_ai_speech(
                    credentials=CREDS,
                    mode="tts",
                    text="Hi",
                    output_format="audio-16khz-128kbitrate-mono-mp3",
                )

        speech_config.set_speech_synthesis_output_format.assert_called_once_with(
            "Audio16Khz128KBitRateMonoMp3"
        )

    def test_tts_empty_text_raises(self):
        sdk = _make_speechsdk()
        with _patch_sdk(sdk):
            with pytest.raises(ValueError, match="text input"):
                azure_ai_speech(credentials=CREDS, mode="tts", text="")
