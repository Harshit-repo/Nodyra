"""Ollama Chat Model and Embeddings supplier nodes.

Uses the ``ollama`` Python SDK (>=0.4) to expose locally-running Ollama
models as first-class ``ai_language_model`` and ``ai_embedding_model``
suppliers without requiring an API key.
"""

from __future__ import annotations

from typing import Any

from noodle.ai_runtime import (
    ChatModelAdapter,
    ChatRequest,
    ChatResponse,
    EmbeddingModelAdapter,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelUsage,
)
from noodle.sdk import node

AI_CATEGORY = "AI"

OLLAMA_CHAT_MODEL_CHOICES = [
    "llama3.2",
    "llama3.1",
    "llama3",
    "mistral",
    "qwen2.5",
    "qwen2.5-coder",
    "gemma2",
    "phi4",
    "deepseek-r1",
    "codellama",
    "dolphin-mistral",
    "vicuna",
]

OLLAMA_EMBEDDING_MODEL_CHOICES = [
    "nomic-embed-text",
    "mxbai-embed-large",
    "all-minilm",
    "snowflake-arctic-embed",
    "bge-m3",
]


class OllamaChatAdapter(ChatModelAdapter):
    """ChatModelAdapter backed by the ollama Python SDK."""

    def __init__(
        self,
        *,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        temperature: float | None = 0.2,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> None:
        self._host = host or "http://localhost:11434"
        self._model = model or "llama3.2"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._extra_options = extra_options or {}

    def complete(self, request: ChatRequest) -> ChatResponse:
        try:
            import ollama
        except ImportError:
            raise ImportError(
                "ollama_chat_model requires the ollama package. "
                "Install with: pip install 'ollama>=0.4'"
            )

        client = ollama.Client(host=self._host)

        messages = [
            {"role": msg.role.value, "content": msg.content}
            for msg in request.messages
        ]

        # Build options dict — adapter config is authoritative for temperature/max_tokens;
        # request.max_tokens overrides when explicitly provided (non-None)
        opts: dict[str, Any] = {**self._extra_options}
        if self._temperature is not None:
            opts["temperature"] = float(self._temperature)
        num_predict = request.max_tokens if request.max_tokens is not None else self._max_tokens
        if num_predict is not None:
            opts["num_predict"] = int(num_predict)

        resp = client.chat(
            model=request.model or self._model,
            messages=messages,
            options=opts or None,
        )

        text = ""
        if hasattr(resp, "message") and hasattr(resp.message, "content"):
            text = str(resp.message.content or "")

        # Ollama response includes prompt_eval_count and eval_count
        usage = ModelUsage()
        if hasattr(resp, "prompt_eval_count") and resp.prompt_eval_count:
            usage = ModelUsage(
                prompt_tokens=int(resp.prompt_eval_count),
                completion_tokens=int(getattr(resp, "eval_count", 0) or 0),
                total_tokens=int(resp.prompt_eval_count) + int(getattr(resp, "eval_count", 0) or 0),
            )

        return ChatResponse(
            text=text,
            model=str(getattr(resp, "model", request.model or self._model)),
            finish_reason=str(getattr(resp, "done_reason", "") or "stop"),
            usage=usage,
            provider="ollama",
        )

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "ollama_chat",
            "host": self._host,
            "model": self._model,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "extra_options": self._extra_options,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OllamaChatAdapter:
        return cls(
            host=str(config.get("host") or "http://localhost:11434"),
            model=str(config.get("model") or "llama3.2"),
            temperature=config.get("temperature"),
            max_tokens=config.get("max_tokens"),
            extra_options=config.get("extra_options"),
        )


class OllamaEmbeddingAdapter(EmbeddingModelAdapter):
    """EmbeddingModelAdapter backed by the ollama Python SDK."""

    def __init__(
        self,
        *,
        host: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
    ) -> None:
        self._host = host or "http://localhost:11434"
        self._model = model or "nomic-embed-text"

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        try:
            import ollama
        except ImportError:
            raise ImportError(
                "ollama_embeddings requires the ollama package. "
                "Install with: pip install 'ollama>=0.4'"
            )

        client = ollama.Client(host=self._host)
        model_id = request.model or self._model

        # ollama SDK >=0.4 accepts a list of strings in `input`
        resp = client.embed(model=model_id, input=request.texts)

        raw_embeddings = getattr(resp, "embeddings", None) or []
        embeddings: list[list[float]] = [
            [float(x) for x in vec] for vec in raw_embeddings
        ]

        return EmbeddingResponse(
            embeddings=embeddings,
            model=model_id,
        )

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "ollama_embedding",
            "host": self._host,
            "model": self._model,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OllamaEmbeddingAdapter:
        return cls(
            host=str(config.get("host") or "http://localhost:11434"),
            model=str(config.get("model") or "nomic-embed-text"),
        )


@node(
    name="Ollama Chat Model",
    id="ollama_chat_model",
    category=AI_CATEGORY,
    role="supplier",
    icon="brand:ollama",
    outputs=["model"],
    output_kinds={"model": "ai_language_model"},
    requirements=["ollama>=0.4"],
    param_groups={"Options": ["temperature", "max_tokens"]},
    params={
        "host": {
            "placeholder": "http://localhost:11434",
            "description": "Ollama server URL (default: http://localhost:11434).",
        },
        "model": {
            "choices": OLLAMA_CHAT_MODEL_CHOICES,
            "placeholder": "llama3.2",
            "description": "Ollama model name. Must already be pulled on the server.",
        },
        "temperature": {
            "description": "Sampling temperature (0–2). Lower values are more deterministic.",
            "group": "Options",
        },
        "max_tokens": {
            "description": "Maximum tokens to generate (num_predict). Leave empty for model default.",
            "group": "Options",
        },
    },
)
def ollama_chat_model(
    host: str = "http://localhost:11434",
    model: str = "llama3.2",
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> OllamaChatAdapter:
    """Supply a locally-running Ollama chat model to downstream AI nodes."""
    return OllamaChatAdapter(
        host=host or "http://localhost:11434",
        model=model or "llama3.2",
        temperature=float(temperature),
        max_tokens=int(max_tokens) if max_tokens is not None else None,
    )


@node(
    name="Ollama Embeddings",
    id="ollama_embeddings",
    category=AI_CATEGORY,
    role="supplier",
    icon="brand:ollama",
    outputs=["model"],
    output_kinds={"model": "ai_embedding_model"},
    requirements=["ollama>=0.4"],
    params={
        "host": {
            "placeholder": "http://localhost:11434",
            "description": "Ollama server URL (default: http://localhost:11434).",
        },
        "model": {
            "choices": OLLAMA_EMBEDDING_MODEL_CHOICES,
            "placeholder": "nomic-embed-text",
            "description": "Ollama embedding model. Must already be pulled on the server.",
        },
    },
)
def ollama_embeddings(
    host: str = "http://localhost:11434",
    model: str = "nomic-embed-text",
) -> OllamaEmbeddingAdapter:
    """Supply a locally-running Ollama embedding model to downstream RAG/vector nodes."""
    return OllamaEmbeddingAdapter(
        host=host or "http://localhost:11434",
        model=model or "nomic-embed-text",
    )
