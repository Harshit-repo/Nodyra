"""Tests for Task 20: Ollama Chat Model and Embeddings supplier nodes."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from nodyra.ai_runtime import (
    AIMessage,
    ChatRequest,
    EmbeddingRequest,
    MessageRole,
)
from nodyra_nodes.ollama_nodes import (
    OllamaChatAdapter,
    OllamaEmbeddingAdapter,
    ollama_chat_model,
    ollama_embeddings,
)


def _make_chat_request(text: str = "Hello") -> ChatRequest:
    return ChatRequest(
        messages=[AIMessage(role=MessageRole.user, content=text)],
        model="llama3.2",
    )


def _make_ollama_mock() -> MagicMock:
    """Return a MagicMock ollama module."""
    mock = MagicMock()

    class FakeClient:
        def __init__(self, host="http://localhost:11434"):
            self._host = host

        def chat(self, model, messages, options=None):
            resp = MagicMock()
            resp.message.content = "Hello from Ollama!"
            resp.model = model
            resp.done_reason = "stop"
            resp.prompt_eval_count = 5
            resp.eval_count = 10
            return resp

        def embed(self, model, input):
            resp = MagicMock()
            n = len(input) if isinstance(input, list) else 1
            resp.embeddings = [[0.1, 0.2, 0.3]] * n
            return resp

    mock.Client = FakeClient
    return mock


# ---------------------------------------------------------------------------
# OllamaChatAdapter tests
# ---------------------------------------------------------------------------


class TestOllamaChatAdapter:
    def test_complete_returns_text(self):
        adapter = OllamaChatAdapter(model="llama3.2")
        mock_ollama = _make_ollama_mock()

        with patch.dict(sys.modules, {"ollama": mock_ollama}):
            req = _make_chat_request("Hi")
            resp = adapter.complete(req)

        assert resp.text == "Hello from Ollama!"
        assert resp.provider == "ollama"
        assert resp.usage.prompt_tokens == 5
        assert resp.usage.completion_tokens == 10

    def test_custom_host_forwarded(self):
        custom_host = "http://my-server:11434"
        adapter = OllamaChatAdapter(host=custom_host, model="mistral")
        mock_ollama = _make_ollama_mock()

        created_hosts: list[str] = []

        original_client = mock_ollama.Client

        class TrackingClient(original_client):
            def __init__(self, host=""):
                created_hosts.append(host)
                super().__init__(host)

        mock_ollama.Client = TrackingClient

        with patch.dict(sys.modules, {"ollama": mock_ollama}):
            adapter.complete(_make_chat_request("test"))

        assert created_hosts == [custom_host]

    def test_temperature_applied(self):
        adapter = OllamaChatAdapter(model="llama3.2", temperature=0.9)
        mock_ollama = _make_ollama_mock()

        captured_opts: list[dict] = []

        original_client = mock_ollama.Client

        class TrackingClient(original_client):
            def chat(self, model, messages, options=None):
                captured_opts.append(options or {})
                return super().chat(model, messages, options)

        mock_ollama.Client = TrackingClient

        with patch.dict(sys.modules, {"ollama": mock_ollama}):
            adapter.complete(_make_chat_request())

        assert captured_opts[0].get("temperature") == 0.9

    def test_max_tokens_sets_num_predict(self):
        adapter = OllamaChatAdapter(model="llama3.2", max_tokens=512)
        mock_ollama = _make_ollama_mock()

        captured_opts: list[dict] = []

        original_client = mock_ollama.Client

        class TrackingClient(original_client):
            def chat(self, model, messages, options=None):
                captured_opts.append(options or {})
                return super().chat(model, messages, options)

        mock_ollama.Client = TrackingClient

        with patch.dict(sys.modules, {"ollama": mock_ollama}):
            adapter.complete(_make_chat_request())

        assert captured_opts[0].get("num_predict") == 512

    def test_import_error_raised_without_ollama(self):
        adapter = OllamaChatAdapter()
        with patch.dict(sys.modules, {"ollama": None}):
            with pytest.raises(ImportError, match="ollama"):
                adapter.complete(_make_chat_request())

    def test_as_config_roundtrip(self):
        adapter = OllamaChatAdapter(
            host="http://localhost:11434",
            model="mistral",
            temperature=0.5,
            max_tokens=256,
        )
        config = adapter.as_config()
        restored = OllamaChatAdapter.from_config(config)
        assert restored._host == adapter._host
        assert restored._model == adapter._model
        assert restored._temperature == adapter._temperature
        assert restored._max_tokens == adapter._max_tokens


# ---------------------------------------------------------------------------
# OllamaEmbeddingAdapter tests
# ---------------------------------------------------------------------------


class TestOllamaEmbeddingAdapter:
    def test_embed_returns_vectors(self):
        adapter = OllamaEmbeddingAdapter(model="nomic-embed-text")
        mock_ollama = _make_ollama_mock()

        with patch.dict(sys.modules, {"ollama": mock_ollama}):
            req = EmbeddingRequest(texts=["hello", "world"], model="nomic-embed-text")
            resp = adapter.embed(req)

        assert len(resp.embeddings) == 2
        assert resp.embeddings[0] == [0.1, 0.2, 0.3]

    def test_model_from_request_overrides_adapter_default(self):
        adapter = OllamaEmbeddingAdapter(model="nomic-embed-text")
        mock_ollama = _make_ollama_mock()

        captured_models: list[str] = []

        original_client = mock_ollama.Client

        class TrackingClient(original_client):
            def embed(self, model, input):
                captured_models.append(model)
                return super().embed(model, input)

        mock_ollama.Client = TrackingClient

        with patch.dict(sys.modules, {"ollama": mock_ollama}):
            req = EmbeddingRequest(texts=["test"], model="mxbai-embed-large")
            adapter.embed(req)

        assert captured_models[0] == "mxbai-embed-large"

    def test_import_error_raised_without_ollama(self):
        adapter = OllamaEmbeddingAdapter()
        with patch.dict(sys.modules, {"ollama": None}):
            req = EmbeddingRequest(texts=["test"], model="nomic-embed-text")
            with pytest.raises(ImportError, match="ollama"):
                adapter.embed(req)

    def test_as_config_roundtrip(self):
        adapter = OllamaEmbeddingAdapter(host="http://gpu-box:11434", model="bge-m3")
        config = adapter.as_config()
        restored = OllamaEmbeddingAdapter.from_config(config)
        assert restored._host == adapter._host
        assert restored._model == adapter._model


# ---------------------------------------------------------------------------
# Node registration and factory
# ---------------------------------------------------------------------------


class TestOllamaNodeFactory:
    def test_ollama_chat_model_returns_adapter(self):
        result = ollama_chat_model(host="http://localhost:11434", model="llama3.2")
        assert isinstance(result, OllamaChatAdapter)
        assert result._model == "llama3.2"

    def test_ollama_embeddings_returns_adapter(self):
        result = ollama_embeddings(host="http://localhost:11434", model="nomic-embed-text")
        assert isinstance(result, OllamaEmbeddingAdapter)
        assert result._model == "nomic-embed-text"

    def test_chat_model_default_host(self):
        result = ollama_chat_model()
        assert result._host == "http://localhost:11434"

    def test_embeddings_default_host(self):
        result = ollama_embeddings()
        assert result._host == "http://localhost:11434"
