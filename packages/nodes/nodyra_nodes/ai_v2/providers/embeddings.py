"""Embedding model adapters — OpenAI-compatible + Cohere, requests-only."""

from __future__ import annotations

from typing import Any

import requests

from nodyra.ai_runtime import (
    EmbeddingModelAdapter,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelUsage,
)

_OPENAI_BASE = "https://api.openai.com/v1"
_COHERE_BASE = "https://api.cohere.ai/v1"


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _expect_json(response: requests.Response, service: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"{service}: HTTP {response.status_code}: {response.text[:800]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{service}: expected JSON response") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"{service}: expected JSON object response")
    return body


class OpenAIEmbeddingAdapter(EmbeddingModelAdapter):
    """Adapter for OpenAI / openai_compatible / Ollama embedding endpoints."""

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        model: str = "text-embedding-3-small",
        provider: str = "openai",
        prompt_price_per_1m_tokens: float | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model or "text-embedding-3-small"
        self._provider = (provider or "openai").strip().lower()
        self._prompt_price_per_1m_tokens = prompt_price_per_1m_tokens

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if self._provider == "ollama":
            base_url = self._base_url or "http://localhost:11434/v1"
            api_key = self._api_key or "ollama"
        elif self._provider == "openai_compatible":
            base_url = self._base_url
            api_key = self._api_key
            if not base_url:
                raise ValueError("openai_compatible embeddings require a base_url")
        else:
            base_url = self._base_url or _OPENAI_BASE
            api_key = self._api_key
            if not api_key:
                raise ValueError("OpenAI embeddings require an api_key")

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = requests.post(
            f"{base_url.rstrip('/')}/embeddings",
            headers=headers,
            json={"model": request.model or self._model, "input": request.texts},
            timeout=max(1, min(300, request.timeout_seconds or 60)),
        )
        body = _expect_json(resp, self._provider)
        data = body.get("data") if isinstance(body.get("data"), list) else []
        ordered = sorted(
            (d for d in data if isinstance(d, dict)),
            key=lambda d: int(d.get("index") or 0),
        )
        embeddings = [
            [float(x) for x in (item.get("embedding") or [])] for item in ordered
        ]
        raw_usage = body.get("usage") or {}
        usage = ModelUsage(
            prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
            total_tokens=int(raw_usage.get("total_tokens") or 0),
        ).with_estimated_cost(
            prompt_price_per_1m_tokens=self._prompt_price_per_1m_tokens
        )
        return EmbeddingResponse(
            embeddings=embeddings,
            model=str(body.get("model") or request.model or self._model),
            usage=usage,
        )

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "openai_embedding",
            "api_key": self._api_key,
            "base_url": self._base_url,
            "model": self._model,
            "provider": self._provider,
            "prompt_price_per_1m_tokens": self._prompt_price_per_1m_tokens,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OpenAIEmbeddingAdapter:
        return cls(
            api_key=str(config.get("api_key") or ""),
            base_url=str(config.get("base_url") or ""),
            model=str(config.get("model") or "text-embedding-3-small"),
            provider=str(config.get("provider") or "openai"),
            prompt_price_per_1m_tokens=config.get("prompt_price_per_1m_tokens"),
        )


class CohereEmbeddingAdapter(EmbeddingModelAdapter):
    """Adapter for Cohere embedding endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "",
        model: str = "embed-english-v3.0",
        input_type: str = "search_document",
    ) -> None:
        if not api_key:
            raise ValueError("CohereEmbeddingAdapter requires api_key")
        self._api_key = api_key
        self._base_url = base_url or _COHERE_BASE
        self._model = model or "embed-english-v3.0"
        self._input_type = input_type or "search_document"

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        resp = requests.post(
            f"{self._base_url.rstrip('/')}/embed",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": request.model or self._model,
                "texts": request.texts,
                "input_type": self._input_type,
            },
            timeout=max(1, min(300, request.timeout_seconds or 60)),
        )
        body = _expect_json(resp, "cohere")
        embeddings = [
            [float(x) for x in emb] for emb in (body.get("embeddings") or [])
        ]
        return EmbeddingResponse(
            embeddings=embeddings,
            model=str(request.model or self._model),
        )

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "cohere_embedding",
            "api_key": self._api_key,
            "base_url": self._base_url,
            "model": self._model,
            "input_type": self._input_type,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> CohereEmbeddingAdapter:
        return cls(
            api_key=str(config.get("api_key") or ""),
            base_url=str(config.get("base_url") or ""),
            model=str(config.get("model") or "embed-english-v3.0"),
            input_type=str(config.get("input_type") or "search_document"),
        )


def embedding_adapter_from_credentials(
    credentials: dict[str, Any] | None,
    *,
    provider: str = "",
    model: str = "",
    prompt_price_per_1m_tokens: float | None = None,
) -> EmbeddingModelAdapter:
    """Build the appropriate ``EmbeddingModelAdapter`` from a credential dict."""
    creds: dict[str, str] = {}
    if isinstance(credentials, dict):
        creds = {str(k): str(v) for k, v in credentials.items() if v not in (None, "")}

    effective = (provider or creds.get("provider") or "openai").strip().lower()
    prompt_rate = (
        _optional_float(prompt_price_per_1m_tokens)
        if prompt_price_per_1m_tokens is not None
        else _optional_float(creds.get("prompt_price_per_1m_tokens"))
    )

    if effective == "cohere":
        return CohereEmbeddingAdapter(
            api_key=creds.get("api_key", ""),
            base_url=creds.get("base_url") or "",
            model=model or creds.get("model") or "embed-english-v3.0",
        )

    return OpenAIEmbeddingAdapter(
        api_key=creds.get("api_key") or "",
        base_url=creds.get("base_url") or "",
        model=model or creds.get("model") or "text-embedding-3-small",
        provider=effective,
        prompt_price_per_1m_tokens=prompt_rate,
    )
