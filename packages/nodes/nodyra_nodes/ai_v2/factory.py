"""Adapter factory — build a ChatModelAdapter from a credential dict.

This bridges the legacy ``llm.py`` credential shape (which uses a flat
``{provider, api_key, base_url, ...}`` dict) and the new adapter classes.
"""

from __future__ import annotations

from typing import Any

from nodyra.ai_runtime import ChatModelAdapter

_ALIASES: dict[str, str] = {
    "openai compatible": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "open router": "openrouter",
    "open-router": "openrouter",
    "azure": "azure_openai",
    "azure-openai": "azure_openai",
    "azure openai": "azure_openai",
}


def _resolve_provider(provider: str, credentials: dict[str, str]) -> str:
    value = (provider or credentials.get("provider") or "openai").strip().lower()
    return _ALIASES.get(value, value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def adapter_from_credentials(
    credentials: dict[str, Any] | None,
    *,
    provider: str = "",
    model: str = "",
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: str = "text",
    timeout_seconds: int = 75,
    prompt_price_per_1m_tokens: float | None = None,
    completion_price_per_1m_tokens: float | None = None,
) -> ChatModelAdapter:
    """Build the appropriate ``ChatModelAdapter`` from a credential dict.

    Parameters
    ----------
    credentials:
        Flat credential dict, typically the decrypted payload of a Nodyra
        credential record.  Expected keys overlap with the legacy
        ``LLM_CREDENTIAL_FIELDS`` list.
    provider:
        Override for the provider key.  Falls back to
        ``credentials["provider"]`` then ``"openai"``.
    model:
        Default model name if not in the credential dict.
    temperature, max_tokens, response_format, timeout_seconds:
        Runtime options forwarded to the adapter.
    """
    creds: dict[str, str] = {}
    if isinstance(credentials, dict):
        creds = {str(k): str(v) for k, v in credentials.items() if v not in (None, "")}

    effective_provider = _resolve_provider(provider, creds)
    prompt_rate = (
        _optional_float(prompt_price_per_1m_tokens)
        if prompt_price_per_1m_tokens is not None
        else _optional_float(creds.get("prompt_price_per_1m_tokens"))
    )
    completion_rate = (
        _optional_float(completion_price_per_1m_tokens)
        if completion_price_per_1m_tokens is not None
        else _optional_float(creds.get("completion_price_per_1m_tokens"))
    )

    if effective_provider == "azure_openai":
        from nodyra_nodes.ai_v2.providers.openai import AzureOpenAIChatAdapter

        return AzureOpenAIChatAdapter(
            api_key=creds.get("api_key", ""),
            azure_endpoint=creds.get("azure_endpoint") or creds.get("base_url") or "",
            deployment=creds.get("deployment") or model or creds.get("model") or "",
            azure_api_version=creds.get("azure_api_version", "2024-02-15-preview"),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            prompt_price_per_1m_tokens=prompt_rate,
            completion_price_per_1m_tokens=completion_rate,
        )

    if effective_provider == "anthropic":
        from nodyra_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        return AnthropicChatAdapter(
            api_key=creds.get("api_key", ""),
            model=model or creds.get("model") or "claude-3-5-haiku-latest",
            base_url=creds.get("base_url") or "",
            anthropic_version=creds.get("anthropic_version", "2023-06-01"),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            prompt_price_per_1m_tokens=prompt_rate,
            completion_price_per_1m_tokens=completion_rate,
        )

    # OpenAI / OpenRouter / Ollama / openai_compatible
    from nodyra_nodes.ai_v2.providers.openai import OpenAIChatAdapter

    return OpenAIChatAdapter(
        api_key=creds.get("api_key") or "",
        base_url=creds.get("base_url") or "",
        model=model or creds.get("model") or "gpt-4.1-mini",
        provider=effective_provider,
        organization=creds.get("organization") or "",
        site_url=creds.get("site_url") or "",
        app_name=creds.get("app_name") or "",
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        prompt_price_per_1m_tokens=prompt_rate,
        completion_price_per_1m_tokens=completion_rate,
    )
