"""AI Chat Model supplier nodes — typed, provider-neutral, WP10.

Nodes in this module output a ``ChatModelAdapter`` on an ``ai_language_model``
port.  Downstream AI Agent / AI Chat nodes receive the adapter directly instead
of receiving a raw credential-dict config.
"""

from __future__ import annotations

from noodle.ai_runtime import ChatModelAdapter
from noodle.sdk import node
from noodle_nodes._creds import cred_multi
from noodle_nodes.ai_v2.factory import adapter_from_credentials

AI_CATEGORY = "AI"

LLM_CREDENTIAL_FIELDS = [
    "provider",
    "api_key",
    "base_url",
    "organization",
    "azure_endpoint",
    "azure_api_version",
    "deployment",
    "site_url",
    "app_name",
    "prompt_price_per_1m_tokens",
    "completion_price_per_1m_tokens",
]

OPENAI_MODEL_CHOICES = [
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
    "o4-mini",
    "o3-mini",
    "openai/gpt-4.1-mini",
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-3.3-70b-instruct",
    "mistralai/mistral-large",
    "llama3.2",
    "llama3.1",
    "qwen2.5",
    "mistral",
    "gemma2",
]

ANTHROPIC_MODEL_CHOICES = [
    "claude-3-5-haiku-latest",
    "claude-3-5-sonnet-latest",
    "claude-3-7-sonnet-latest",
    "claude-opus-4-5",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3.5-haiku",
]

OPENAI_COMPATIBLE_PROVIDER_CHOICES = [
    "openai",
    "openrouter",
    "openai_compatible",
    "ollama",
]

AZURE_API_VERSIONS = [
    "2025-01-01-preview",
    "2024-12-01-preview",
    "2024-08-01-preview",
    "2024-06-01",
]


@node(
    name="AI Chat Model - OpenAI",
    id="ai_chat_model_openai",
    category=AI_CATEGORY,
    role="supplier",
    icon="ai",
    outputs=["model"],
    output_kinds={"model": "ai_language_model"},
    param_groups={
        "Options": [
            "temperature",
            "max_tokens",
            "response_format",
            "timeout_seconds",
            "prompt_price_per_1m_tokens",
            "completion_price_per_1m_tokens",
        ]
    },
    params={
        "credentials": {
            **cred_multi("llm_provider", "LLM provider credential", LLM_CREDENTIAL_FIELDS),
            "description": "Credential for the selected OpenAI-compatible provider.",
        },
        "provider": {
            "choices": OPENAI_COMPATIBLE_PROVIDER_CHOICES,
            "description": "Provider: openai, openrouter, ollama, or openai_compatible.",
        },
        "model": {
            "choices": OPENAI_MODEL_CHOICES,
            "placeholder": "gpt-4.1-mini",
            "description": "Model ID.",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "temperature": {
            "description": "Sampling temperature (0–2).",
            "group": "Options",
        },
        "max_tokens": {
            "description": "Maximum response tokens.",
            "group": "Options",
        },
        "response_format": {
            "choices": ["text", "json_object"],
            "description": "Response format for compatible models.",
            "group": "Options",
        },
        "timeout_seconds": {
            "description": "HTTP timeout in seconds (1–300).",
            "group": "Options",
        },
        "prompt_price_per_1m_tokens": {
            "description": "Optional input-token price in USD per 1M tokens.",
            "group": "Options",
        },
        "completion_price_per_1m_tokens": {
            "description": "Optional output-token price in USD per 1M tokens.",
            "group": "Options",
        },
    },
)
def ai_chat_model_openai(
    credentials: dict | None = None,
    provider: str = "openai",
    model: str = "gpt-4.1-mini",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    response_format: str = "text",
    timeout_seconds: int = 75,
    prompt_price_per_1m_tokens: float | None = None,
    completion_price_per_1m_tokens: float | None = None,
) -> ChatModelAdapter:
    """Supply an OpenAI/Ollama/OpenRouter chat model adapter to downstream AI nodes."""
    return adapter_from_credentials(
        credentials or {},
        provider=provider,
        model=model,
        temperature=float(temperature),
        max_tokens=max_tokens,
        response_format=response_format or "text",
        timeout_seconds=int(timeout_seconds or 75),
        prompt_price_per_1m_tokens=prompt_price_per_1m_tokens,
        completion_price_per_1m_tokens=completion_price_per_1m_tokens,
    )


@node(
    name="AI Chat Model - Anthropic",
    id="ai_chat_model_anthropic",
    category=AI_CATEGORY,
    role="supplier",
    icon="ai",
    outputs=["model"],
    output_kinds={"model": "ai_language_model"},
    param_groups={
        "Options": [
            "temperature",
            "max_tokens",
            "timeout_seconds",
            "prompt_price_per_1m_tokens",
            "completion_price_per_1m_tokens",
        ],
    },
    params={
        "credentials": {
            **cred_multi(
                "anthropic_api_key",
                "Anthropic API credential",
                ["api_key"],
            ),
            "description": "Anthropic API key credential.",
        },
        "model": {
            "choices": ANTHROPIC_MODEL_CHOICES,
            "placeholder": "claude-3-5-haiku-latest",
            "description": "Anthropic Claude model ID.",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "temperature": {
            "description": "Sampling temperature (0–1).",
            "group": "Options",
        },
        "max_tokens": {
            "description": "Maximum response tokens.",
            "group": "Options",
        },
        "timeout_seconds": {
            "description": "HTTP timeout in seconds (1–300).",
            "group": "Options",
        },
        "prompt_price_per_1m_tokens": {
            "description": "Optional input-token price in USD per 1M tokens.",
            "group": "Options",
        },
        "completion_price_per_1m_tokens": {
            "description": "Optional output-token price in USD per 1M tokens.",
            "group": "Options",
        },
    },
)
def ai_chat_model_anthropic(
    credentials: dict | None = None,
    model: str = "claude-3-5-haiku-latest",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_seconds: int = 75,
    prompt_price_per_1m_tokens: float | None = None,
    completion_price_per_1m_tokens: float | None = None,
) -> ChatModelAdapter:
    """Supply an Anthropic Claude chat model adapter to downstream AI nodes."""
    return adapter_from_credentials(
        credentials or {},
        provider="anthropic",
        model=model,
        temperature=float(temperature),
        max_tokens=max_tokens,
        timeout_seconds=int(timeout_seconds or 75),
        prompt_price_per_1m_tokens=prompt_price_per_1m_tokens,
        completion_price_per_1m_tokens=completion_price_per_1m_tokens,
    )


@node(
    name="AI Chat Model - Azure OpenAI",
    id="ai_chat_model_azure",
    category=AI_CATEGORY,
    role="supplier",
    icon="ai",
    outputs=["model"],
    output_kinds={"model": "ai_language_model"},
    param_groups={
        "Options": [
            "temperature",
            "max_tokens",
            "api_version",
            "timeout_seconds",
            "prompt_price_per_1m_tokens",
            "completion_price_per_1m_tokens",
        ],
    },
    params={
        "credentials": {
            **cred_multi(
                "azure_openai_api_key",
                "Azure OpenAI credential",
                ["azure_endpoint", "api_key", "deployment"],
            ),
            "description": "Azure OpenAI credential with endpoint, key and deployment.",
        },
        "model": {
            "placeholder": "gpt-4o-mini",
            "description": "Deployment/model name on Azure.",
            "load_options": "llm_models",
            "depends_on": ["credentials"],
        },
        "temperature": {
            "description": "Sampling temperature (0–2).",
            "group": "Options",
        },
        "max_tokens": {
            "description": "Maximum response tokens.",
            "group": "Options",
        },
        "api_version": {
            "choices": AZURE_API_VERSIONS,
            "description": "Azure OpenAI API version.",
            "group": "Options",
        },
        "timeout_seconds": {
            "description": "HTTP timeout in seconds (1–300).",
            "group": "Options",
        },
        "prompt_price_per_1m_tokens": {
            "description": "Optional input-token price in USD per 1M tokens.",
            "group": "Options",
        },
        "completion_price_per_1m_tokens": {
            "description": "Optional output-token price in USD per 1M tokens.",
            "group": "Options",
        },
    },
)
def ai_chat_model_azure(
    credentials: dict | None = None,
    model: str = "gpt-4o-mini",
    temperature: float = 0.2,
    max_tokens: int | None = None,
    api_version: str = "2025-01-01-preview",
    timeout_seconds: int = 75,
    prompt_price_per_1m_tokens: float | None = None,
    completion_price_per_1m_tokens: float | None = None,
) -> ChatModelAdapter:
    """Supply an Azure OpenAI chat model adapter to downstream AI nodes."""
    creds = dict(credentials or {})
    if api_version:
        creds.setdefault("azure_api_version", api_version)
    return adapter_from_credentials(
        creds,
        provider="azure_openai",
        model=model,
        temperature=float(temperature),
        max_tokens=max_tokens,
        timeout_seconds=int(timeout_seconds or 75),
        prompt_price_per_1m_tokens=prompt_price_per_1m_tokens,
        completion_price_per_1m_tokens=completion_price_per_1m_tokens,
    )
