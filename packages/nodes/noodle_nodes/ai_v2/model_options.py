"""Dynamic-option loaders for LLM / embedding model dropdowns.

Each loader fetches the provider's live model catalogue when a credential is
supplied, and degrades to a small curated list on any error so the dropdown is
never empty (offline, no list API, bad key, etc.).
"""

from __future__ import annotations

from typing import Any

import httpx

from noodle_nodes.integrations_v2.dynamic_options import register_loader

CURATED_CHAT_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "o3-mini"],
    "openai_compatible": ["gpt-4.1-mini", "gpt-4o-mini"],
    "anthropic": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
    "openrouter": ["openai/gpt-4.1-mini", "anthropic/claude-3.7-sonnet"],
    "ollama": ["llama3.1", "qwen2.5", "gemma2"],
    "azure_openai": ["gpt-4o-mini", "gpt-4o"],
}

CURATED_EMBEDDING_MODELS: dict[str, list[str]] = {
    "openai": ["text-embedding-3-small", "text-embedding-3-large"],
    "openai_compatible": ["text-embedding-3-small"],
    "ollama": ["nomic-embed-text", "mxbai-embed-large"],
    "cohere": ["embed-english-v3.0", "embed-multilingual-v3.0"],
}

_PROVIDER_ALIASES = {
    "openai compatible": "openai_compatible",
    "open router": "openrouter",
    "open-router": "openrouter",
}


def _effective_provider(provider: str | None, credentials: dict[str, str]) -> str:
    value = (provider or credentials.get("provider") or "openai").strip().lower()
    return _PROVIDER_ALIASES.get(value, value)


def _fetch_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    with httpx.Client(timeout=10) as client:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        body = response.json()
    return body if isinstance(body, dict) else {}


def _auth_header(api_key: str) -> dict[str, str] | None:
    return {"Authorization": f"Bearer {api_key}"} if api_key else None


def normalize_api_base(url: str | None) -> str:
    """Strip a trailing operation path so we can append ``/models`` etc.

    Users (and some credential defaults) paste the full chat endpoint, e.g.
    ``https://openrouter.ai/api/v1/chat/completions``. Appending ``/models`` to
    that yields a 404. Trim known operation suffixes back to the API base.
    """
    base = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/")


def _chat_models_for(provider: str, api_key: str, base_url: str) -> list[str]:
    base = normalize_api_base(base_url)
    if provider == "ollama":
        url = base or "http://localhost:11434"
        if url.endswith("/v1"):
            url = url[:-3]
        data = _fetch_json("GET", f"{url}/api/tags")
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    if provider == "openrouter":
        url = base or "https://openrouter.ai/api/v1"
        data = _fetch_json("GET", f"{url}/models", headers=_auth_header(api_key))
        return [m["id"] for m in data.get("data", []) if m.get("id")]
    if provider == "anthropic":
        data = _fetch_json(
            "GET",
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        return [m["id"] for m in data.get("data", []) if m.get("id")]
    # openai, openai_compatible, azure_openai → OpenAI-compatible /models
    url = base or "https://api.openai.com/v1"
    data = _fetch_json("GET", f"{url}/models", headers=_auth_header(api_key))
    return [m["id"] for m in data.get("data", []) if m.get("id")]


def _as_options(models: list[str]) -> list[dict[str, str]]:
    return [{"value": m, "label": m, "description": ""} for m in models]


def llm_models(
    credentials: Any = None,
    provider: str | None = None,
    base_url: str | None = None,
    **_kwargs: Any,
) -> list[dict[str, str]]:
    creds = (
        {str(k): str(v) for k, v in credentials.items()}
        if isinstance(credentials, dict)
        else {}
    )
    prov = _effective_provider(provider, creds)
    api_key = creds.get("api_key") or creds.get("token") or ""
    url = base_url or creds.get("base_url") or ""
    try:
        models = _chat_models_for(prov, api_key, url)
    except Exception:
        models = []
    if not models:
        models = CURATED_CHAT_MODELS.get(prov, CURATED_CHAT_MODELS["openai"])
    return _as_options(models)


def embedding_models(
    credentials: Any = None,
    provider: str | None = None,
    base_url: str | None = None,
    **_kwargs: Any,
) -> list[dict[str, str]]:
    creds = (
        {str(k): str(v) for k, v in credentials.items()}
        if isinstance(credentials, dict)
        else {}
    )
    prov = _effective_provider(provider, creds)
    api_key = creds.get("api_key") or creds.get("token") or ""
    url = base_url or creds.get("base_url") or ""
    try:
        if prov in {"openai", "openai_compatible", "ollama"}:
            models = [m for m in _chat_models_for(prov, api_key, url) if "embed" in m.lower()]
        else:
            models = []
    except Exception:
        models = []
    if not models:
        models = CURATED_EMBEDDING_MODELS.get(prov, CURATED_EMBEDDING_MODELS["openai"])
    return _as_options(models)


register_loader("llm_models", llm_models)
register_loader("embedding_models", embedding_models)
