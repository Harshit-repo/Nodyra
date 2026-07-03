"""Tests for text_to_speech_file and speech_to_text_file nodes."""

from unittest.mock import MagicMock, patch

import pytest

from nodyra_nodes.ai_extra import speech_to_text_file, text_to_speech_file

_FAKE_MP3 = b"ID3\x03\x00" + b"\x00" * 100  # fake MP3 bytes

_AUDIO_REF = {
    "__nodyra_artifact__": True,
    "version": 1,
    "artifact_id": "abc123",
    "run_id": "run1",
    "node_id": "tts_node",
    "name": "speech.mp3",
    "kind": "audio",
    "content_type": "audio/mpeg",
    "size_bytes": 1024,
    "storage_backend": "local",
    "storage_key": "runs/run1/tts_node/abc123-speech.mp3",
}


# ---------------------------------------------------------------------------
# text_to_speech_file tests
# ---------------------------------------------------------------------------


def test_tts_file_returns_artifact():
    """Successful TTS call returns the write_bytes artifact dict."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = _FAKE_MP3
    mock_response.headers = {"content-type": "audio/mpeg"}

    expected_ref = {"__nodyra_artifact__": True, "artifact_id": "abc"}

    with patch("nodyra_nodes.ai_extra.requests.post", return_value=mock_response) as _mock_post, \
         patch("nodyra_nodes.ai_extra.write_bytes", return_value=expected_ref) as mock_write:
        result = text_to_speech_file(credentials="sk-test", text="Hello world")

    mock_write.assert_called_once()
    _, kwargs = mock_write.call_args
    assert kwargs.get("kind") == "audio"
    assert result == expected_ref


def test_tts_file_speed_param():
    """Speed parameter is forwarded in the JSON payload to the API."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = _FAKE_MP3
    mock_response.headers = {"content-type": "audio/mpeg"}

    with patch("nodyra_nodes.ai_extra.requests.post", return_value=mock_response) as mock_post, \
         patch("nodyra_nodes.ai_extra.write_bytes", return_value={}):
        text_to_speech_file(credentials="sk-test", text="Hello", speed=2.0)

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["speed"] == 2.0


def test_tts_file_cache_key_in_metadata():
    """cache_key is stored in metadata when provided."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = _FAKE_MP3
    mock_response.headers = {"content-type": "audio/mpeg"}

    with patch("nodyra_nodes.ai_extra.requests.post", return_value=mock_response), \
         patch("nodyra_nodes.ai_extra.write_bytes", return_value={}) as mock_write:
        text_to_speech_file(credentials="sk-test", text="Hello", cache_key="greeting_v1")

    mock_write.assert_called_once()
    _, kwargs = mock_write.call_args
    assert kwargs.get("metadata", {}).get("cache_key") == "greeting_v1"


def test_tts_file_raises_on_http_error():
    """HTTP 4xx from the TTS API raises RuntimeError with status in message."""
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "rate limited"

    with patch("nodyra_nodes.ai_extra.requests.post", return_value=mock_response), \
         patch("nodyra_nodes.ai_extra.write_bytes"):
        with pytest.raises(RuntimeError, match="HTTP 429"):
            text_to_speech_file(credentials="sk-test", text="Hello")


def test_tts_file_falls_back_to_wired_input():
    """When text is empty, wired input is used as the synthesis text."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = _FAKE_MP3
    mock_response.headers = {"content-type": "audio/mpeg"}

    with patch("nodyra_nodes.ai_extra.requests.post", return_value=mock_response) as mock_post, \
         patch("nodyra_nodes.ai_extra.write_bytes", return_value={}):
        text_to_speech_file(input="Wired text", credentials="sk-test", text="")

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["input"] == "Wired text"


# ---------------------------------------------------------------------------
# speech_to_text_file tests
# ---------------------------------------------------------------------------


def test_stt_file_transcribes_artifact():
    """Successful STT call returns the parsed JSON transcript."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "Hello"}

    with patch("nodyra_nodes.ai_extra.read_artifact_bytes", return_value=b"audio") as _mock_read, \
         patch("nodyra_nodes.ai_extra.requests.post", return_value=mock_response):
        result = speech_to_text_file(input=_AUDIO_REF, credentials="sk-test")

    assert result == {"text": "Hello"}


def test_stt_file_rejects_non_audio_artifact():
    """Artifact ref with kind != 'audio' raises ValueError."""
    bad_ref = {**_AUDIO_REF, "kind": "binary"}

    with patch("nodyra_nodes.ai_extra.read_artifact_bytes", return_value=b"audio"):
        with pytest.raises(ValueError, match="audio artifact"):
            speech_to_text_file(input=bad_ref, credentials="sk-test")


def test_stt_file_size_guard():
    """Artifact exceeding 25 MB raises ValueError mentioning '25 MB'."""
    big_ref = {**_AUDIO_REF, "size_bytes": 30 * 1024 * 1024}

    with patch("nodyra_nodes.ai_extra.read_artifact_bytes", return_value=b"audio"):
        with pytest.raises(ValueError, match="25 MB"):
            speech_to_text_file(input=big_ref, credentials="sk-test")


def test_stt_file_rejects_non_artifact_input():
    """Plain string input (not an artifact ref) raises ValueError."""
    with pytest.raises(ValueError):
        speech_to_text_file(input="not an artifact", credentials="sk-test")


def test_stt_file_sends_language_and_prompt():
    """language and prompt params are forwarded in the multipart data."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "Hola"}

    with patch("nodyra_nodes.ai_extra.read_artifact_bytes", return_value=b"audio"), \
         patch("nodyra_nodes.ai_extra.requests.post", return_value=mock_response) as mock_post:
        speech_to_text_file(
            input=_AUDIO_REF,
            credentials="sk-test",
            language="es",
            prompt="medical terms",
        )

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    data = kwargs.get("data", {})
    assert data.get("language") == "es"
    assert data.get("prompt") == "medical terms"
